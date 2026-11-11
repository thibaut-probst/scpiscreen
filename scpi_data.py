"""Google Sheets loading, validation and SCPI scoring helpers."""

from __future__ import annotations

import csv
import io
import logging
import math
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

logger = logging.getLogger("scpiscreen.data")

# Paste the Google Sheets document ID here. The sheet must be shared as
# "Anyone with the link" with the Viewer role only.
SHEET_ID = "10RwJl0A2dvoHjY6LmOFQvT7nghF3-n73hf-Aa9FJBtE"
WORKSHEETS = {
    "data": "Data",
    "scores": "Scores",
}
# Weights tab: two header rows (category, criterion) then one row per profile.
WEIGHTS_WORKSHEET = "Pondérations"
WEIGHTS_HEADER_ROWS = 2
UNCATEGORIZED_LABEL = "Autres critères"

# Header of the Data column whose values are the SCPI names written in the Scores tab
# ("Catégorie SCPI"). Leave empty to detect it automatically: the Data column (the "SCPI"
# column by default) whose values match the most Scores names is used.
DATA_NAME_COLUMN = ""

CATEGORY_ORDER = [
    "Structure financière",
    "Diversification",
    "Sécurité locative",
    "Maturité",
    "Rentabilité",
]
PROFILE_ORDER = ["Équilibre", "Stabilité", "Performance", "Assurance-vie", "Nue-Propriété"]
PROFILE_SCORE_COLUMNS = {
    "Équilibre": "Note Equilibre",
    "Stabilité": "Note Stabilité",
    "Performance": "Note Performance",
    "Assurance-vie": "Note Assurance-vie",
    "Nue-Propriété": "Note Nue-Propriété",
}
# Every score column the Scores tab should provide.
SCORE_COLUMNS = [*PROFILE_SCORE_COLUMNS.values(), *CATEGORY_ORDER]
SCORE_MIN = 0.0
SCORE_MAX = 100.0

SHEET_HOST = "docs.google.com"
SHEET_TIMEOUT_SECONDS = 12
MAX_SHEET_RESPONSE_BYTES = 8 * 1024 * 1024

# Availability limits: the page embeds one detail sheet per SCPI, so the row count is capped.
MAX_SCPI_ROWS = 300
MAX_WEIGHT_COLUMNS = 100
WEIGHT_MAX = 100.0

# Cache: data stays valid for 15 minutes; after a failed refresh the last good data is kept
# (marked as stale) and the source is not queried again for RETRY_AFTER seconds.
SHEET_CACHE_TTL_SECONDS = 900
SHEET_RETRY_AFTER_SECONDS = 60

# Missing-data check. A column that is "NA" for almost every SCPI silently skews the scores
# or leaves the table empty, so a warning is shown above the ranking. Two layers:
#   1. every weighted criterion of the weights tab (blank cells count as missing, and the
#      warning reports the weight of the criterion in each profile);
#   2. every other column of the checked tabs (only explicit "NA" cells count, so that
#      optional columns that are simply left blank do not raise a warning).
MISSING_CRITERION_THRESHOLD = 0.5  # flagged when strictly more than 50 % of the rows are missing
MAX_MISSING_CRITERIA_DISPLAYED = 10
# Tabs checked, in order. A criterion is looked up in the first tab that has its column.
CRITERIA_TABS = ("scores", "data")
# Cell contents (normalized by _text_key) that mean "no value".
MISSING_TOKENS = frozenset(
    {"", "na", "n/a", "n.a.", "nd", "n.d.", "#n/a", "#div/0!", "#value!", "#valeur!",
     "nan", "null", "none", "-", "–", "—"}
)
EXPLICIT_MISSING_TOKENS = MISSING_TOKENS - {""}


class SheetLoadError(RuntimeError):
    """Raised when a configured worksheet cannot be loaded. The message is safe to display."""


class _SheetRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only towards the Google Sheets host, over HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https" or target.hostname != SHEET_HOST:
            raise SheetLoadError("La source Google Sheets a tenté une redirection non autorisée.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _csv_url(sheet_id: str, worksheet: str, header_rows: int | None = None) -> str:
    if not sheet_id or any(
        not character.isascii() or not (character.isalnum() or character in "-_")
        for character in sheet_id
    ):
        raise SheetLoadError("L’identifiant du Google Sheet est invalide.")
    encoded_sheet = urllib.parse.quote(worksheet, safe="")
    url = (
        f"https://{SHEET_HOST}/spreadsheets/d/{sheet_id}/gviz/tq"
        f"?tqx=out:csv&sheet={encoded_sheet}"
    )
    if header_rows is not None:
        url += f"&headers={int(header_rows)}"
    parsed_url = urllib.parse.urlsplit(url)
    if parsed_url.scheme != "https" or parsed_url.hostname != SHEET_HOST:
        raise SheetLoadError("La source Google Sheets doit utiliser HTTPS.")
    return url


def _read_google_csv(sheet_id: str, worksheet: str, header_rows: int | None = None) -> str:
    request = urllib.request.Request(
        _csv_url(sheet_id, worksheet, header_rows),
        headers={"User-Agent": "SCPIScreen/1.0"},
    )
    opener = urllib.request.build_opener(_SheetRedirectHandler())
    try:
        with opener.open(request, timeout=SHEET_TIMEOUT_SECONDS) as response:
            final_host = urllib.parse.urlsplit(response.geturl()).hostname
            content_type = response.headers.get_content_type()
            content = response.read(MAX_SHEET_RESPONSE_BYTES + 1)
    except SheetLoadError:
        raise
    except Exception as error:
        # Details stay in the server log; the visitor gets a generic message.
        logger.warning("Google Sheet request failed for %r: %s", worksheet, error)
        raise SheetLoadError(f"Impossible de lire l’onglet « {worksheet} ».") from error

    if final_host != SHEET_HOST:
        raise SheetLoadError(f"L’onglet « {worksheet} » a été servi depuis un hôte inattendu.")
    if content_type == "text/html":
        # A private sheet returns a sign-in page instead of CSV.
        raise SheetLoadError(f"L’onglet « {worksheet} » n’est pas accessible publiquement.")
    if len(content) > MAX_SHEET_RESPONSE_BYTES:
        raise SheetLoadError(f"L’onglet « {worksheet} » dépasse la taille autorisée.")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SheetLoadError(f"L’onglet « {worksheet} » n’est pas un texte UTF-8 valide.") from error


def _normalize_header(value: Any) -> str:
    """Header with every run of whitespace (non-breaking and doubled spaces included) collapsed."""
    return " ".join(str(value).split())


def _read_sheet(sheet_id: str, worksheet: str) -> list[dict[str, str]]:
    content = _read_google_csv(sheet_id, worksheet)
    try:
        rows = list(csv.DictReader(io.StringIO(content)))
    except csv.Error as error:
        raise SheetLoadError(f"L’onglet « {worksheet} » n’est pas un CSV valide.") from error
    if not rows:
        raise SheetLoadError(f"L’onglet « {worksheet} » est vide ou mal nommé.")
    return [
        {_normalize_header(key): (value or "").strip() for key, value in row.items() if key}
        for row in rows
    ]


def load_google_sheet(sheet_id: str, worksheets: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    """Load all configured tabs from a Google Sheet published as CSV (tabs are fetched in parallel)."""
    with ThreadPoolExecutor(max_workers=max(1, len(worksheets))) as pool:
        futures = {key: pool.submit(_read_sheet, sheet_id, name) for key, name in worksheets.items()}
        data = {key: future.result() for key, future in futures.items()}
    data_rows = data.get("data", [])
    if data_rows and "SCPI" not in data_rows[0]:
        data_tab = worksheets.get("data", "Data")
        raise SheetLoadError(f"La colonne « SCPI » est absente de l’onglet « {data_tab} ».")
    return data


# ---- Number parsing ---------------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")
_YEARS_SUFFIX = re.compile(r"ans$", re.IGNORECASE)


def parse_number(value: Any) -> float | None:
    """The only number parser of the app.

    Accepts a comma or a dot as decimal separator, any kind of space (including the
    non-breaking spaces used in French number formats), a trailing "%" or "ans".
    Returns None for an empty, non-numeric or non-finite value (nan, inf).
    """
    if value is None:
        return None
    text = _WHITESPACE.sub("", str(value)).replace("%", "").replace(",", ".")
    text = _YEARS_SUFFIX.sub("", text)
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _number(value: Any, default: float = 0.0) -> float:
    """Lenient number parser: unusable values return the default."""
    number = parse_number(value)
    return default if number is None else number


# Name kept for the Scores tab validation: None when the cell is empty or not a finite number.
parse_score = parse_number


def validated_score(value: Any) -> float | None:
    """A score as displayed: a finite number within 0 to 100, otherwise None (unavailable)."""
    number = parse_score(value)
    if number is None or not SCORE_MIN <= number <= SCORE_MAX:
        return None
    return number


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _text_key(name)).strip("-") or "scpi"


def _unique_id(name: str, used: set[str]) -> str:
    base = _slug(name)
    candidate, counter = base, 2
    while candidate in used:
        candidate = f"{base}-{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def resolve_name_column(data: dict[str, list[dict[str, str]]]) -> str:
    """Header of the Data column that holds the names used in the Scores tab."""
    if DATA_NAME_COLUMN:
        return DATA_NAME_COLUMN
    rows = data.get("data", [])
    score_keys = {
        _text_key(label)
        for label in (row.get("Catégorie SCPI", "").strip() for row in data.get("scores", []))
        if label
    }
    if not rows or not score_keys:
        return "SCPI"

    def matches(column: str) -> int:
        return sum(1 for row in rows if _text_key(row.get(column, "")) in score_keys)

    best, best_count = "SCPI", matches("SCPI")
    for column in rows[0]:
        if column == "SCPI":
            continue
        count = matches(column)
        if count > best_count:
            best, best_count = column, count
    return best


def _row_name(row: dict[str, str], column: str) -> str:
    return (row.get(column) or "").strip() or row.get("SCPI", "").strip()


def build_rankings(data: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    """Join SCPI data with the final, precomputed scores from the Scores worksheet.

    The join is strict: a SCPI is matched to a score row by name only (case, accents and
    spacing are ignored). There is no positional fallback. A SCPI without a score row is
    returned with has_scores=False and is never ranked. Duplicated score labels use the first row.

    A score that is empty, not numeric or outside 0 to 100 is None: the SCPI is not ranked for
    that profile (a category score of None is shown as unavailable). No score is ever invented.
    """
    rows = data.get("data", [])
    score_rows = data.get("scores", [])
    score_by_name: dict[str, dict[str, str]] = {}
    for row in score_rows:
        label = row.get("Catégorie SCPI", "").strip()
        if label:
            score_by_name.setdefault(_text_key(label), row)

    name_column = resolve_name_column(data)
    results: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for row in rows:
        name = _row_name(row, name_column)
        if not name:
            continue
        if len(results) >= MAX_SCPI_ROWS:
            break
        score_row = score_by_name.get(_text_key(name))
        has_scores = score_row is not None
        scores = score_row or {}
        profile_scores: dict[str, float | None] = {
            profile: validated_score(scores.get(column)) if has_scores else None
            for profile, column in PROFILE_SCORE_COLUMNS.items()
        }
        category_scores: dict[str, float | None] = {
            category: validated_score(scores.get(category)) if has_scores else None
            for category in CATEGORY_ORDER
        }
        main_sectors = row.get("Secteurs majoritaires", "").strip() or row.get("Stratégie sectorielle", "").strip()
        main_regions = row.get("Régions majoritaires", "").strip() or row.get("Région majoritaire", "").strip()
        item = {
            "scpi_id": _unique_id(name, used_ids),
            "name": name,
            "has_scores": has_scores,
            "discount": row.get("Décote", "").strip(),
            "liquidity_rate": row.get("Taux de liquidité", "").strip(),
            "debt_rate": row.get("Taux d'endettement", "").strip(),
            "bonus_louve": row.get("Bonus Louve", "").strip(),
            "tri_demembrement": row.get("TRI Max Démembrement", "").strip(),
            "duree_tri_demembrement": row.get("Durée TRI Max Démembrement", "").strip(),
            "souscriptions": row.get("Souscriptions", "").strip(),
            "retraits": row.get("Retraits", "").strip(),
            "valeur_souscription": row.get("Valeur de souscription", "").strip(),
            "valeur_reconstitution": row.get("Valeur de reconstitution", "").strip(),
            "part_de_secteur_majoritaire": row.get("Part de secteur majoritaire", "").strip(),
            "part_logements_hors_france": row.get("Part de logements hors de France", "").strip(),
            "part_de_region_majoritaire": row.get("Part de région majoritaire", "").strip(),
            "tof": row.get("TOF", "").strip(),
            "top": row.get("TOP", "").strip(),
            "tof_top": row.get("TOF-TOP", "").strip(),
            "capitalisation_m": row.get("Capitalisation (M)", "").strip(),
            "creation": row.get("Création", "").strip(),
            "frais_entree": row.get("Frais d'entrée", "").strip(),
            "frais_gestion": row.get("Frais de gestion", "").strip(),
            "pga": row.get("PGA", "").strip(),
            "tri": row.get("TRI", "").strip(),
            "walb": row.get("WALB", "").strip(),
            "revalorisation_annuelle_historique": row.get("Revalorisation annuelle moyenne historique", "").strip(),
            "revalorisation_annuelle_recente": row.get("Revalorisation annuelle moyenne récente", "").strip(),
            "nombre_actifs": row.get("Nombre d'actifs", "").strip(),
            "main_sectors": main_sectors,
            "main_regions": main_regions,
            "profile_scores": profile_scores,
            "category_scores": category_scores,
        }
        results.append(item)
    return results


def validate_scores(data: dict[str, list[dict[str, str]]]) -> list[str]:
    """Return human-readable warnings about the Scores tab (empty list when all is well)."""
    score_rows = data.get("scores", [])
    if not score_rows:
        return []
    headers = score_rows[0].keys()
    messages: list[str] = []
    for column in ["Catégorie SCPI", *SCORE_COLUMNS]:
        if column not in headers:
            messages.append(f"La colonne « {column} » est absente de l’onglet « Scores ».")

    profile_columns = set(PROFILE_SCORE_COLUMNS.values())
    invalid_labels: dict[str, list[str]] = {}
    for row in score_rows:
        label = row.get("Catégorie SCPI", "").strip()
        if not label:
            continue  # Blank spreadsheet rows are not SCPI rows.
        for column in SCORE_COLUMNS:
            if column not in headers:
                continue
            if validated_score(row.get(column)) is None:
                invalid_labels.setdefault(column, []).append(label)

    for column, labels in invalid_labels.items():
        consequence = (
            "Ces SCPI ne sont pas classées pour ce profil."
            if column in profile_columns
            else "Cette valeur est affichée comme indisponible."
        )
        messages.append(
            f"Valeur absente ou hors de 0 à 100 dans « {column} » pour : {_summarize(labels)}. {consequence}"
        )
    return messages


def _summarize(labels: list[str], limit: int = 5) -> str:
    listed = ", ".join(labels[:limit])
    remaining = len(labels) - limit
    return f"{listed} et {remaining} autre(s)" if remaining > 0 else listed


def _duplicates(labels: list[str]) -> list[str]:
    counts = Counter(_text_key(label) for label in labels)
    seen: set[str] = set()
    repeated = []
    for label in labels:
        key = _text_key(label)
        if counts[key] > 1 and key not in seen:
            seen.add(key)
            repeated.append(label)
    return repeated


def validate_join(data: dict[str, list[dict[str, str]]]) -> list[str]:
    """Warnings about the link between the Data and Scores tabs (empty list when all is well)."""
    rows = data.get("data", [])
    score_rows = data.get("scores", [])
    if not rows and not score_rows:
        return []
    name_column = resolve_name_column(data)
    data_names = [name for name in (_row_name(row, name_column) for row in rows) if name]
    score_labels = [
        label for label in (row.get("Catégorie SCPI", "").strip() for row in score_rows) if label
    ]
    data_keys = {_text_key(name) for name in data_names}
    score_keys = {_text_key(label) for label in score_labels}

    messages: list[str] = []
    unmatched = [name for name in data_names if _text_key(name) not in score_keys]
    if unmatched:
        messages.append(
            "Aucune ligne de l’onglet « Scores » ne correspond à : "
            f"{_summarize(unmatched)}. Ces SCPI ne sont pas classées."
        )
    orphans = [label for label in score_labels if _text_key(label) not in data_keys]
    if orphans:
        messages.append(
            "Ces lignes de l’onglet « Scores » ne correspondent à aucune SCPI de l’onglet « Data » "
            f"et sont ignorées : {_summarize(orphans)}."
        )
    repeated_data = _duplicates(data_names)
    if repeated_data:
        messages.append(f"Nom en double dans l’onglet « Data » : {_summarize(repeated_data)}.")
    repeated_scores = _duplicates(score_labels)
    if repeated_scores:
        messages.append(
            "Nom en double dans l’onglet « Scores » (la première ligne est utilisée) : "
            f"{_summarize(repeated_scores)}."
        )
    if len(data_names) > MAX_SCPI_ROWS:
        messages.append(
            f"Seules les {MAX_SCPI_ROWS} premières SCPI de l’onglet « Data » sont affichées "
            f"({len(data_names)} trouvées)."
        )
    return messages


def is_missing_value(value: Any) -> bool:
    """True when a cell is empty or holds an "NA"-like token (case and accents ignored)."""
    return _text_key("" if value is None else value) in MISSING_TOKENS


def _scpi_rows(data: dict[str, list[dict[str, str]]], tab: str) -> list[dict[str, str]]:
    """Rows of a tab that belong to a SCPI (blank spreadsheet rows are not counted)."""
    rows = data.get(tab, [])
    if tab == "scores":
        return [row for row in rows if row.get("Catégorie SCPI", "").strip()]
    name_column = resolve_name_column(data)
    return [row for row in rows if _row_name(row, name_column)]


def _find_criterion_column(rows: list[dict[str, str]], category: str, criterion: str) -> str | None:
    """Header of the column that holds a criterion, tolerant to case, accents and spacing.

    Both "Criterion" and the merged "Category Criterion" label are accepted.
    """
    if not rows:
        return None
    wanted = {_text_key(criterion)}
    if category and category != UNCATEGORIZED_LABEL:
        wanted.add(_text_key(f"{category} {criterion}"))
    for header in rows[0]:
        if _text_key(header) in wanted:
            return header
    return None


def _excluded_columns(data: dict[str, list[dict[str, str]]], tab: str) -> set[str]:
    """Columns that are never checked for "NA": names/labels and the final scores."""
    if tab == "scores":
        return {"Catégorie SCPI", *SCORE_COLUMNS}  # the final scores are checked by validate_scores
    return {"SCPI", resolve_name_column(data)}


def validate_missing_criteria(
    data: dict[str, list[dict[str, str]]],
    matrix: dict[str, Any],
    threshold: float = MISSING_CRITERION_THRESHOLD,
) -> list[str]:
    """Warn when a column is "NA" for more than `threshold` of the SCPI rows.

    `matrix` is the result of build_weight_matrix() (pass {"profiles": [], "criteria": []}
    when the weights tab is unavailable: the second layer still works). Two layers:
      1. the weighted criteria, found by their header in CRITERIA_TABS (first tab that has the
         column); the warning gives their weight in each profile. Only the column really
         evaluated here is skipped by layer 2;
      2. every other column of those tabs, counting only explicit "NA" cells. This covers the
         criteria without any weight and the same-named column of the tab that layer 1 did not
         evaluate (for example the raw "Data" column behind a criterion note of "Scores").
    """
    profiles: list[str] = matrix.get("profiles", [])
    criteria: list[dict[str, Any]] = matrix.get("criteria", [])
    totals = {
        profile: sum(criterion["weights"].get(profile, 0.0) for criterion in criteria)
        for profile in profiles
    }
    rows_by_tab = {tab: _scpi_rows(data, tab) for tab in CRITERIA_TABS}

    # (share, top weight impact, sort label, message)
    findings: list[tuple[float, float, str, str]] = []
    checked: set[tuple[str, str]] = set()  # (tab, column) already evaluated by layer 1

    # Layer 1: weighted criteria.
    for criterion in criteria:
        weights = criterion["weights"]
        if not any(weights.get(profile, 0.0) > 0 for profile in profiles):
            continue  # no weight: it cannot change a score, layer 2 still checks the column
        for tab in CRITERIA_TABS:
            rows = rows_by_tab[tab]
            column = _find_criterion_column(rows, criterion["category"], criterion["name"])
            if column is None:
                continue
            checked.add((tab, column))
            missing = sum(1 for row in rows if is_missing_value(row.get(column)))
            share = missing / len(rows)
            if share > threshold:
                impacts = [
                    (profile, weights[profile] / totals[profile] * 100)
                    for profile in profiles
                    if weights.get(profile, 0.0) > 0 and totals[profile] > 0
                ]
                impact_text = ", ".join(f"{profile} {value:.0f} %" for profile, value in impacts)
                findings.append((
                    share,
                    max((value for _, value in impacts), default=0.0),
                    criterion["name"],
                    f"Attention : le critère « {criterion['name']} » n'est pas disponible pour "
                    f"{missing} SCPI sur {len(rows)} ({share * 100:.0f} %). "
                    f"Poids dans le scoring : {impact_text}. "
                    "Les notes qui en dépendent peuvent être faussées.",
                ))
            break  # only the first tab that has the column is evaluated

    # Layer 2: every other column (explicit "NA" cells only).
    for tab in CRITERIA_TABS:
        rows = rows_by_tab[tab]
        if not rows:
            continue
        excluded = _excluded_columns(data, tab)
        tab_name = WORKSHEETS.get(tab, tab)
        for column in rows[0]:
            if column in excluded or (tab, column) in checked:
                continue
            missing = sum(
                1 for row in rows if _text_key(row.get(column) or "") in EXPLICIT_MISSING_TOKENS
            )
            share = missing / len(rows)
            if share > threshold:
                findings.append((
                    share,
                    0.0,
                    column,
                    f"Attention : la donnée « {column} » (onglet « {tab_name} ») n'est pas disponible "
                    f"pour {missing} SCPI sur {len(rows)} ({share * 100:.0f} %). "
                    "Le calcul des scores qui l'utilisent peut être faussé.",
                ))

    findings.sort(key=lambda finding: (-finding[0], -finding[1], finding[2]))
    messages = [finding[3] for finding in findings[:MAX_MISSING_CRITERIA_DISPLAYED]]
    hidden = len(findings) - MAX_MISSING_CRITERIA_DISPLAYED
    if hidden > 0:
        messages.append(
            f"{hidden} autre(s) colonne(s) sont aussi en NA pour plus de {threshold * 100:.0f} % des SCPI."
        )
    return messages


_LIGATURES = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "’": "'", "‘": "'", "ʼ": "'"})


def _text_key(value: Any) -> str:
    """Normalize a label for tolerant comparison (case, accents, ligatures, apostrophes, spacing)."""
    decomposed = unicodedata.normalize("NFKD", str(value).translate(_LIGATURES))
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.casefold().split())


def text_key(value: Any) -> str:
    """Public alias of the tolerant label normalization, used for sorting and for the search."""
    return _text_key(value)


def load_weights_sheet(sheet_id: str, worksheet: str = WEIGHTS_WORKSHEET) -> list[list[str]]:
    """Load the weights tab as raw rows (the header labels repeat, so no DictReader)."""
    content = _read_google_csv(sheet_id, worksheet, header_rows=WEIGHTS_HEADER_ROWS)
    try:
        rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(content))]
    except csv.Error as error:
        raise SheetLoadError(f"L’onglet « {worksheet} » n’est pas un CSV valide.") from error
    if not any(any(row) for row in rows):
        raise SheetLoadError(f"L’onglet « {worksheet} » est vide ou mal nommé.")
    return rows


def _split_category(label: str) -> tuple[str, str]:
    """Split a merged "Category Criterion" header label into its two parts."""
    normalized = " ".join(label.split())
    for category in CATEGORY_ORDER:
        prefix = f"{category} "
        if normalized.casefold().startswith(prefix.casefold()):
            return category, normalized[len(prefix):].strip()
    return "", normalized


def build_weight_matrix(rows: list[list[str]]) -> dict[str, Any]:
    """Turn the raw weights tab into profiles, categories and weighted criteria.

    Two layouts are supported, because Google's CSV endpoint may either merge the
    two header rows into one label per column ("Category Criterion") or return
    the "Catégorie" and "Critère" rows untouched.
    """
    profile_keys = {_text_key(profile): profile for profile in PROFILE_ORDER}
    category_row: list[str] | None = None
    criteria_row: list[str] | None = None
    header_row: list[str] | None = None
    profile_rows: dict[str, list[str]] = {}

    for row in rows:
        if not any(row):
            continue
        label_key = _text_key(row[0])
        if label_key in profile_keys:
            profile_rows.setdefault(profile_keys[label_key], row)
        elif label_key == "critere" and criteria_row is None:
            criteria_row = row
        elif label_key == "categorie" and category_row is None:
            category_row = row
        elif header_row is None and not profile_rows:
            header_row = row

    if not profile_rows:
        raise SheetLoadError(
            "Aucune ligne de profil (Équilibre, Stabilité…) trouvée dans l’onglet « Pondérations »."
        )

    # Each column: (column index, category, criterion name).
    columns: list[tuple[int, str, str]] = []
    if criteria_row is not None:
        for index in range(1, len(criteria_row)):
            criterion = " ".join(criteria_row[index].split())
            category = ""
            if category_row is not None and index < len(category_row):
                category = " ".join(category_row[index].split())
            columns.append((index, category, criterion))
    elif header_row is not None:
        for index in range(1, len(header_row)):
            category, criterion = _split_category(header_row[index])
            columns.append((index, category, criterion))
    columns = [
        column for column in columns if column[2] and _text_key(column[2]) != "total"
    ][:MAX_WEIGHT_COLUMNS]
    if not columns:
        raise SheetLoadError("Aucun critère trouvé dans l’onglet « Pondérations ».")

    profiles = [profile for profile in PROFILE_ORDER if profile in profile_rows]
    weights_by_profile: dict[str, list[float]] = {}
    for profile in profiles:
        row = profile_rows[profile]
        values = [
            min(max(0.0, _number(row[index])), WEIGHT_MAX) if index < len(row) else 0.0
            for index, _, _ in columns
        ]
        # Fractions such as 0.04 (instead of 4 %) are converted to percentages.
        if 0 < sum(values) <= 1.5:
            values = [min(value * 100, WEIGHT_MAX) for value in values]
        weights_by_profile[profile] = values

    criteria = [
        {
            "name": criterion,
            "category": category or UNCATEGORIZED_LABEL,
            "weights": {profile: weights_by_profile[profile][position] for profile in profiles},
        }
        for position, (_, category, criterion) in enumerate(columns)
    ]

    found_categories = [criterion["category"] for criterion in criteria]
    categories = [category for category in CATEGORY_ORDER if category in found_categories]
    for category in found_categories:
        if category not in categories:
            categories.append(category)

    return {"profiles": profiles, "categories": categories, "criteria": criteria}


# ---- Cache with stale-if-error -------------------------------------------------------

_V = TypeVar("_V")


@dataclass(frozen=True)
class StoreResult(Generic[_V]):
    """Cached value. Treat it as read-only: it is shared between all sessions."""

    value: _V
    version: int  # incremented on every successful refresh
    loaded_at: float  # timestamp of the last successful load
    stale: bool  # True when the last refresh failed and older data is served


class TTLStore(Generic[_V]):
    """Process-wide cache: one refresh at a time, last good value kept when the source fails."""

    def __init__(
        self,
        loader: Callable[[], _V],
        *,
        ttl: float,
        retry_after: float,
        name: str,
    ) -> None:
        self._loader = loader
        self._ttl = ttl
        self._retry_after = retry_after
        self._name = name
        self._state_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._value: _V | None = None
        self._has_value = False
        self._version = 0
        self._loaded_at = 0.0
        self._next_attempt = 0.0
        self._last_error = ""

    def _result(self, stale: bool) -> StoreResult[_V]:
        # Callers hold _state_lock and know _has_value is True.
        return StoreResult(self._value, self._version, self._loaded_at, stale)  # type: ignore[arg-type]

    def _fresh(self, now: float) -> bool:
        return self._has_value and now - self._loaded_at < self._ttl

    def get(self) -> StoreResult[_V]:
        """Return the cached value, refreshing it when needed.

        Raises SheetLoadError only when no value has ever been loaded successfully.
        """
        with self._state_lock:
            now = time.time()
            if self._fresh(now):
                return self._result(False)
            if now < self._next_attempt:  # a refresh failed recently: do not hammer the source
                if self._has_value:
                    return self._result(True)
                raise SheetLoadError(self._last_error)
            have_value = self._has_value

        # Without any value everybody waits for the single refresh; otherwise stale data is served meanwhile.
        if not self._refresh_lock.acquire(blocking=not have_value):
            with self._state_lock:
                return self._result(True)
        try:
            with self._state_lock:
                if self._fresh(time.time()):  # another thread refreshed while we waited
                    return self._result(False)
            try:
                value = self._loader()
            except SheetLoadError as error:
                message = str(error)
                logger.warning("Refresh of %r failed: %s", self._name, message)
            except Exception:
                logger.exception("Unexpected error while refreshing %r", self._name)
                message = "Erreur interne lors du chargement des données."
            else:
                with self._state_lock:
                    self._value = value
                    self._has_value = True
                    self._version += 1
                    self._loaded_at = time.time()
                    self._next_attempt = 0.0
                    self._last_error = ""
                    return self._result(False)
            with self._state_lock:
                self._next_attempt = time.time() + self._retry_after
                self._last_error = message
                if self._has_value:
                    return self._result(True)
            raise SheetLoadError(message)
        finally:
            self._refresh_lock.release()


# Shared by every session. Streamlit keeps imported modules alive between reruns.
DATA_STORE: TTLStore[dict[str, list[dict[str, str]]]] = TTLStore(
    lambda: load_google_sheet(SHEET_ID, WORKSHEETS),
    ttl=SHEET_CACHE_TTL_SECONDS,
    retry_after=SHEET_RETRY_AFTER_SECONDS,
    name="data",
)
WEIGHTS_STORE: TTLStore[list[list[str]]] = TTLStore(
    lambda: load_weights_sheet(SHEET_ID),
    ttl=SHEET_CACHE_TTL_SECONDS,
    retry_after=SHEET_RETRY_AFTER_SECONDS,
    name="weights",
)

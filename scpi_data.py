"""Google Sheets loading and SCPI scoring helpers."""

from __future__ import annotations

import csv
import io
import urllib.parse
import urllib.request
from typing import Any

# Paste the Google Sheets document ID here. Leave empty to use sample data.
SHEET_ID = "10RwJl0A2dvoHjY6LmOFQvT7nghF3-n73hf-Aa9FJBtE"
WORKSHEETS = {
    "data": "Data",
    "scores": "Scores",
}

CATEGORY_ORDER = [
    "Structure financière",
    "Diversification",
    "Sécurité locative",
    "Maturité",
    "Rentabilité",
]
PROFILE_ORDER = ["Équilibre", "Stabilité", "Performance", "Assurance-vie"]
MAX_SHEET_RESPONSE_BYTES = 8 * 1024 * 1024

class SheetLoadError(RuntimeError):
    """Raised when a configured worksheet cannot be loaded."""


def _csv_url(sheet_id: str, worksheet: str) -> str:
    if not sheet_id or any(
        not character.isascii() or not (character.isalnum() or character in "-_")
        for character in sheet_id
    ):
        raise SheetLoadError("L’identifiant du Google Sheet est invalide.")
    encoded_sheet = urllib.parse.quote(worksheet, safe="")
    url = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq"
        f"?tqx=out:csv&sheet={encoded_sheet}"
    )
    parsed_url = urllib.parse.urlsplit(url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "docs.google.com":
        raise SheetLoadError("La source Google Sheets doit utiliser HTTPS.")
    return url


def _read_google_csv(sheet_id: str, worksheet: str) -> str:
    request = urllib.request.Request(
        _csv_url(sheet_id, worksheet),
        headers={"User-Agent": "SCPIScreen/1.0"},
    )
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler())
    try:
        with opener.open(request, timeout=12) as response:
            content = response.read(MAX_SHEET_RESPONSE_BYTES + 1)
    except Exception as error:
        raise SheetLoadError(
            f"Impossible de lire l’onglet « {worksheet} » : {error}"
        ) from error

    if len(content) > MAX_SHEET_RESPONSE_BYTES:
        raise SheetLoadError(f"L’onglet « {worksheet} » dépasse la taille autorisée.")
    return content.decode("utf-8-sig")


def _read_sheet(sheet_id: str, worksheet: str) -> list[dict[str, str]]:
    content = _read_google_csv(sheet_id, worksheet)
    rows = list(csv.DictReader(io.StringIO(content)))
    if not rows:
        raise SheetLoadError(f"L’onglet « {worksheet} » est vide ou mal nommé.")
    return [
        {str(key).strip(): (value or "").strip() for key, value in row.items() if key}
        for row in rows
    ]


def load_google_sheet(sheet_id: str, worksheets: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    """Load all configured tabs from a Google Sheet published as CSV."""
    data: dict[str, list[dict[str, str]]] = {}
    for key, tab_name in worksheets.items():
        data[key] = _read_sheet(sheet_id, tab_name)
    return data


def _number(value: Any, default: float = 0.0) -> float:
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().replace("%", "").replace(" ", "").replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return default


def _name_key(value: Any) -> str:
    return " ".join(str(value).casefold().split())


def _score_rows_are_aligned(
    data_rows: list[dict[str, str]], score_rows: list[dict[str, str]]
) -> bool:
    if not data_rows or len(data_rows) != len(score_rows):
        return False

    checked_rows = 0
    for data_row, score_row in zip(data_rows, score_rows):
        liquidity_rate = data_row.get("Taux de liquidité", "").strip()
        if not liquidity_rate:
            continue
        expected_score = max(0, min(100, _number(liquidity_rate)))
        actual_score = _number(
            score_row.get("Structure financière Taux de liquidité")
        )
        if expected_score != actual_score:
            return False
        checked_rows += 1

    return checked_rows > 0


def build_rankings(data: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    """Join SCPI data with the final, precomputed scores from the Scores worksheet."""
    rows = data.get("data", [])
    score_rows = data.get("scores", [])
    score_by_name = {
        _name_key(row.get("Catégorie SCPI", "")): row
        for row in score_rows
        if row.get("Catégorie SCPI", "").strip()
    }
    scores_are_row_aligned = _score_rows_are_aligned(rows, score_rows)

    results = []
    for row_index, row in enumerate(rows):
        name = row.get("SCPI", "").strip()
        if not name:
            continue
        code_name = _name_key(name)
        score_row = score_by_name.get(code_name, {})
        if not score_row and scores_are_row_aligned:
            score_row = score_rows[row_index]
            name = score_row.get("Catégorie SCPI", "").strip() or name
        profile_columns = {
            "Équilibre": "Note Equilibre",
            "Stabilité": "Note Stabilité",
            "Performance": "Note Performance",
            "Assurance-vie": "Note Assurance-vie",
        }
        profile_scores = {
            profile: _number(score_row.get(column, 0.0))
            for profile, column in profile_columns.items()
        }
        category_scores = {
            "Structure financière": _number(score_row.get("Structure financière", 0.0)),
            "Diversification": _number(score_row.get("Diversification", 0.0)),
            "Sécurité locative": _number(score_row.get("Sécurité locative", 0.0)),
            "Maturité": _number(score_row.get("Maturité", 0.0)),
            "Rentabilité": _number(score_row.get("Rentabilité", 0.0)),
        }
        main_sectors = row.get("Secteurs majoritaires", "").strip() or row.get("Stratégie sectorielle", "").strip()
        main_regions = row.get("Régions majoritaires", "").strip() or row.get("Région majoritaire", "").strip()
        item = {
            "scpi_id": _name_key(name).replace(" ", "-"),
            "name": name,
            "manager": "",
            "strategy": row.get("Stratégie sectorielle", "").strip(),
            "market": row.get("Région majoritaire", "").strip(),
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
            "part_logements_idf": row.get("Part de logements en IdF", "").strip(),
            "part_logements_regions": row.get("Part de logements en régions", "").strip(),
            "part_logements_hors_france": row.get("Part de logements hors de  France", "").strip(),
            "part_de_region_majoritaire": row.get("Part de région majoritaire", "").strip(),
            "regions_majoritaires": row.get("Régions majoritaires", "").strip(),
            "tof": row.get("TOF", "").strip(),
            "top": row.get("TOP", "").strip(),
            "tof_top": row.get("TOF-TOP", "").strip(),
            "capitalisation": row.get("Capitalisation", "").strip(),
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
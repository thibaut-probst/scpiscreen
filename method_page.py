"""HTML builders for the "Méthode de classement" page.

All functions return single-line HTML strings (no blank lines, no indentation) so
that Streamlit's Markdown renderer never mistakes them for code blocks.
"""

from __future__ import annotations

import html
import math
from typing import Any

NBSP = "\u00a0"
TOTAL_TOLERANCE = 0.1
HEAT_MAX_PERCENT = 55
TOP_CRITERIA_PER_PROFILE = 5


def format_weight(value: float) -> str:
    """Format a weight as a French percentage; zero (or an unusable value) is shown as a dash."""
    if not math.isfinite(value) or value <= 0:
        return "—"
    if abs(value - round(value)) < 0.05:
        text = f"{round(value):d}"
    else:
        text = f"{value:.1f}".replace(".", ",")
    return f"{text}{NBSP}%"


def _heat_style(value: float, max_weight: float) -> str:
    """Inline CSS variable used to shade a weight cell proportionally to its weight."""
    if not math.isfinite(value) or not math.isfinite(max_weight) or value <= 0 or max_weight <= 0:
        return ""
    heat = round(min(value / max_weight, 1.0) * HEAT_MAX_PERCENT)
    return f" style='--heat:{heat}%'"


def _weight_cell(value: float, max_weight: float) -> str:
    if not math.isfinite(value) or value <= 0:
        return "<td class='weight-cell is-zero'>—</td>"
    return f"<td class='weight-cell'{_heat_style(value, max_weight)}>{format_weight(value)}</td>"


def _section_heading(kicker: str, title: str) -> str:
    return (
        "<div class='section-heading'><div>"
        f"<div class='section-kicker'>{html.escape(kicker)}</div>"
        f"<div class='section-title'>{html.escape(title)}</div>"
        "</div></div>"
    )


def build_steps_html() -> str:
    """Three-step explanation of how a ranking is produced."""
    steps = [
        (
            "Une note par critère",
            "Chaque indicateur (taux d’occupation, décote, TRI, frais, ancienneté…) "
            "est converti en une note sur 100 à partir des données publiques collectées.",
        ),
        (
            "Un poids par profil",
            "Chaque profil d’investissement attribue un poids à chaque critère. "
            "Les poids d’un même profil totalisent 100 %.",
        ),
        (
            "Un classement",
            "La note finale d’une SCPI est la somme de ses notes de critères multipliées "
            "par leur poids. Les SCPI sont classées de la meilleure à la moins bonne note.",
        ),
    ]
    cards = "".join(
        "<div class='method-step'>"
        f"<span class='method-step-number'>{number}</span>"
        f"<div class='method-step-title'>{html.escape(title)}</div>"
        f"<div class='method-step-text'>{html.escape(text)}</div>"
        "</div>"
        for number, (title, text) in enumerate(steps, start=1)
    )
    return f"<div class='method-steps'>{cards}</div>"


def _category_totals(matrix: dict[str, Any], profile: str) -> dict[str, float]:
    totals = {category: 0.0 for category in matrix["categories"]}
    for criterion in matrix["criteria"]:
        totals[criterion["category"]] += criterion["weights"][profile]
    return totals


def build_profile_cards_html(matrix: dict[str, Any]) -> str:
    """One card per profile listing its heaviest criteria and dominant category."""
    cards = []
    for profile in matrix["profiles"]:
        # sorted() is stable: ties keep the order of the sheet.
        ranked = sorted(matrix["criteria"], key=lambda item: -item["weights"][profile])
        top = [item for item in ranked if item["weights"][profile] > 0][:TOP_CRITERIA_PER_PROFILE]
        totals = _category_totals(matrix, profile)
        dominant = max(matrix["categories"], key=lambda category: totals[category])
        items = "".join(
            "<li>"
            f"<span class='priority-name'>{html.escape(item['name'])}</span>"
            f"<span class='priority-weight'>{format_weight(item['weights'][profile])}</span>"
            "</li>"
            for item in top
        )
        cards.append(
            "<div class='profile-card'>"
            f"<div class='profile-card-name'>{html.escape(profile)}</div>"
            f"<div class='profile-card-focus'>Catégorie dominante : {html.escape(dominant)} "
            f"({format_weight(totals[dominant])})</div>"
            f"<ul class='priority-list'>{items}</ul>"
            "</div>"
        )
    return f"<div class='profile-cards'>{''.join(cards)}</div>"


def build_weights_table_html(matrix: dict[str, Any]) -> str:
    """Summary table: criteria grouped by category, one weight column per profile."""
    profiles = matrix["profiles"]
    criteria = matrix["criteria"]
    max_weight = max(
        (item["weights"][profile] for item in criteria for profile in profiles), default=0.0
    )

    header = "<th scope='col'>Critère</th>" + "".join(
        f"<th scope='col'>{html.escape(profile)}</th>" for profile in profiles
    )

    body = []
    grand_totals = {profile: 0.0 for profile in profiles}
    for category in matrix["categories"]:
        members = [item for item in criteria if item["category"] == category]
        subtotal_cells = "".join(
            f"<td class='weight-cell'>{format_weight(sum(item['weights'][profile] for item in members))}</td>"
            for profile in profiles
        )
        body.append(
            f"<tr class='group-row'><th scope='row'>{html.escape(category)}</th>{subtotal_cells}</tr>"
        )
        for item in members:
            cells = "".join(_weight_cell(item["weights"][profile], max_weight) for profile in profiles)
            body.append(
                f"<tr><th scope='row' class='criterion-name'>{html.escape(item['name'])}</th>{cells}</tr>"
            )
            for profile in profiles:
                grand_totals[profile] += item["weights"][profile]

    total_cells = "".join(
        "<td class='weight-cell"
        + (" is-off" if abs(grand_totals[profile] - 100) > TOTAL_TOLERANCE else "")
        + f"'>{format_weight(grand_totals[profile])}</td>"
        for profile in profiles
    )
    body.append(f"<tr class='total-row'><th scope='row'>Total</th>{total_cells}</tr>")

    return (
        "<div class='weights-scroll'><table class='weights-table'>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody>"
        "</table></div>"
    )


def build_method_page_html(matrix: dict[str, Any]) -> str:
    """Assemble the full methodology page."""
    criteria_count = len(matrix["criteria"])
    categories_count = len(matrix["categories"])
    intro = (
        "<p class='method-intro'>Chaque SCPI est évaluée sur "
        f"<strong>{criteria_count} critères</strong> répartis en "
        f"<strong>{categories_count} catégories</strong>. Le poids de chaque critère dépend du "
        "profil d’investissement choisi : c’est ce qui donne un classement différent selon "
        "que l’on recherche la stabilité, la performance ou un placement en nue-propriété.</p>"
    )
    return (
        "<section class='method-page'>"
        + _section_heading("Méthodologie", "Comment le classement est établi")
        + intro
        + build_steps_html()
        + _section_heading("Profils", "Ce qui compte le plus pour chaque profil")
        + build_profile_cards_html(matrix)
        + _section_heading("Pondérations", "Poids de chaque critère par profil")
        + "<p class='method-intro'>Chaque valeur est la part de la note finale attribuée au critère. "
        "Les lignes colorées regroupent les critères par catégorie et en indiquent le total.</p>"
        + "<p class='mobile-hint'>Faites défiler le tableau horizontalement pour voir tous les profils.</p>"
        + build_weights_table_html(matrix)
        + "</section>"
    )

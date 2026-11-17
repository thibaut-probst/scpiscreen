"""Legal notice and privacy / cookies information, opened from the footer in dialogs.

Nothing personal is committed to the (public) repository. Who publishes the site, how to reach
them and who hosts it are read from the [legal] table of .streamlit/secrets.toml (git-ignored;
on Streamlit Community Cloud paste the same TOML in "Secrets"):

    [legal]
    contact_email     = "..."   # required: questions and exercise of data rights
    publisher_name    = ""      # leave EMPTY to publish anonymously (private individual, non
                                # professional); fill in to be identified
    publisher_address = ""      # only used when a name is given
    publisher_phone   = ""      # only used when a name is given
    host_name         = "Snowflake Inc. (Streamlit Community Cloud)"
    host_address      = "..."   # required: take it from the host's legal pages
    host_phone        = "..."   # required for the legal notice

Environment variables SCPISCREEN_CONTACT_EMAIL, SCPISCREEN_PUBLISHER_NAME, ... (same names in upper
case, prefixed with SCPISCREEN_) are still read when a key is absent from secrets.toml.

The texts below are templates written for what this application really does. They are not legal
advice: check them against your situation (professional or not, hosting, tracking) before
publishing, and update LEGAL_LAST_UPDATE whenever you change them.
"""

from __future__ import annotations

import logging
import os
import re

import streamlit as st

logger = logging.getLogger("scpiscreen")

LEGAL_LAST_UPDATE = "15/11/2026"
REPOSITORY_URL = "https://github.com/thibaut-probst/scpiscreen"
FORMULA_URL = "https://docs.google.com/spreadsheets/d/10RwJl0A2dvoHjY6LmOFQvT7nghF3-n73hf-Aa9FJBtE/"
LOUVE_DATABASE_URL = "https://docs.google.com/spreadsheets/d/1McJtTdsId_xjdpAV7WQbj6COlz5yCOOIiJsIj45tpcU/"
DEFAULT_HOST_NAME = "Snowflake Inc. (Streamlit Community Cloud)"
PLACEHOLDER = ""
DEFAULT_LOG_RETENTION_DAYS = 90

_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+!|~])")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


SECRETS_TABLE = "legal"


def _setting(key: str) -> str:
    """Value of a legal setting: [legal] in secrets.toml first, then SCPISCREEN_<KEY> in the environment."""
    try:
        table = st.secrets.get(SECRETS_TABLE)
        value = table.get(key) if table is not None else None
        if value is not None and str(value).strip():
            return str(value).strip()
    except Exception:  # no secrets file, or an unexpected shape: fall back to the environment
        pass
    return os.environ.get(f"SCPISCREEN_{key.upper()}", "").strip()


def _escape(value: str) -> str:
    """Escape text coming from the environment before showing it as Markdown."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", value)


def _retention_days() -> int:
    try:
        days = int(os.environ.get("SCPISCREEN_LOG_RETENTION_DAYS", "").strip() or DEFAULT_LOG_RETENTION_DAYS)
    except ValueError:
        days = DEFAULT_LOG_RETENTION_DAYS
    return max(1, min(days, 3650))


def missing_settings() -> list[str]:
    """Settings the published texts need; the app logs them at start so they are not forgotten."""
    return [f"{SECRETS_TABLE}.{key}" for key in ("contact_email", "host_address", "host_phone") if not _setting(key)]


if missing_settings():
    logger.warning("Legal pages: missing %s in .streamlit/secrets.toml (placeholders are shown)", ", ".join(missing_settings()))


def _contact() -> str:
    email = _setting("contact_email")
    if not email:
        return PLACEHOLDER
    if _EMAIL.match(email):
        return f"[{_escape(email)}](mailto:{email})"
    return _escape(email)


def build_legal_notice() -> str:
    name = _setting("publisher_name")
    if name:
        lines = [f"**{_escape(name)}**"]
        if _setting("publisher_address"):
            lines.append(_escape(_setting("publisher_address")))
        if _setting("publisher_phone"):
            lines.append(f"Téléphone : {_escape(_setting('publisher_phone'))}")
        lines.append(f"Contact : {_contact()}")
        publisher = "  \n".join(lines)
        director = f"{_escape(name)}, éditeur du site."
    else:
        publisher = (
            "Ce site est édité à titre non professionnel par une personne physique qui a choisi de "
            "préserver son anonymat, comme la loi pour la confiance dans l’économie numérique (LCEN) le permet "
            "aux éditeurs non professionnels. Son identité a été communiquée à l’hébergeur, tenu au secret "
            f"professionnel, qui peut la transmettre à l’autorité judiciaire.  \nContact : {_contact()}"
        )
        director = "L’éditeur du site."

    host_name = _escape(_setting("host_name") or DEFAULT_HOST_NAME)
    host = [f"**{host_name}**", _escape(_setting("host_address")) or f"Adresse : {PLACEHOLDER}"]
    host.append(f"Téléphone : {_escape(_setting('host_phone')) or PLACEHOLDER}")
    host_block = "  \n".join(host)

    return f"""
**Éditeur du site**

{publisher}

**Directeur de la publication**

{director}

**Hébergeur**

{host_block}

**Nature du site**

SCPIScreen est un outil quantitatif d’analyse des SCPI. Il ne tient pas compte de la situation personnelle
de l’utilisateur : il ne constitue ni un conseil en investissement personnalisé, ni du démarchage financier.
Voir l’avertissement de responsabilité en bas de page.

**Licences et sources**

- Le [code source]({REPOSITORY_URL}) et les [formules de calcul]({FORMULA_URL}) sont distribués sous licence GNU GPL v3.
- Les données de marché proviennent de la [base de données publique de Louve Invest]({LOUVE_DATABASE_URL}),
  distribuée par son éditeur sous licence GPL (source citée ; données modifiées et complétées).
  Cet outil est indépendant de Louve Invest.

*Dernière mise à jour : {LEGAL_LAST_UPDATE}.*
"""


def build_privacy_notice() -> str:
    days = _retention_days()
    host = _escape(_setting("host_name") or DEFAULT_HOST_NAME)
    # To adapt once you have checked, in the browser, which cookies the deployed site really sets:
    # the paragraph on the hosting platform's own cookies must match what you found.
    return f"""
**Qui est responsable ?**

L’éditeur du site (voir les mentions légales). Contact pour toute question ou pour exercer vos droits : {_contact()}.

**Quelles données sont traitées ?**

- **Aucune donnée saisie par le visiteur n’est collectée pour établir le classement.** La recherche et les
  filtres s’exécutent dans votre navigateur et ne sont envoyés à aucun serveur.
- **Journal de sécurité du contrôle d’accès** (si un code d’accès est demandé) : date, type d’événement
  (connexion réussie ou échouée, blocage) et une empreinte pseudonymisée de l’adresse IP, calculée avec une clé secrète : l’adresse
  elle-même n’est pas conservée. Finalité : protéger l’accès contre les tentatives répétées. Base légale : intérêt
  légitime (sécurité du site). Conservation : {days} jours. Le code d’accès n’est jamais enregistré.
- **Données techniques liées à l’hébergement** (adresse IP, journaux de connexion) : le site est hébergé par
  {host}, qui les traite pour assurer l’hébergement et peut aussi les traiter pour son propre compte, selon sa
  politique de confidentialité (des transferts hors de l’Union européenne sont possibles). L’éditeur reste
  responsable des informations données sur ce site.

**Cookies et stockage dans votre navigateur**

| Nom | Rôle | Durée | Consentement |
| --- | --- | --- | --- |
| `__Host-scpiscreen_access` | Mémorise que vous avez saisi le code d’accès (jeton signé). Absent si aucun code n’est demandé. | 1 an | Non : nécessaire à l’authentification |
| `_streamlit_csrf` | Protection du site contre les requêtes falsifiées | Session | Non : nécessaire à la sécurité |
| `scpiscreen.ranking.v1` (stockage de session) | Garde vos filtres et votre recherche pendant que l’onglet reste ouvert, y compris quand vous changez de profil | Fermeture de l’onglet | Non : préférence d’affichage, jamais transmise |

L’application elle-même n’utilise ni publicité, ni outil de mesure d’audience. La plateforme d’hébergement peut
toutefois déposer ses propres traceurs lorsque vous consultez le site, et l’éditeur ne peut pas les désactiver depuis
l’application. Ils sont décrits dans la politique de confidentialité et de cookies de {host}. Les cookies en question observés sont notamment :

| Nom | Rôle habituel |
| --- | --- |
| `_ga`, `_gid`, `_dc_gtm_*` | Mesure d’audience (Google Analytics / Tag Manager) |
| `ajs_anonymous_id` | Identifiant anonyme d’analyse (Segment) |
| `_dd_s` | Supervision des performances (Datadog) |
| `proxy-tracking-id` | Identifiant de suivi de la plateforme |
| `streamlit_session` | Session de la plateforme |

Des services tiers de la plateforme (par exemple une page d’état de service) peuvent aussi déposer leurs propres
cookies. Pour les refuser, vous pouvez paramétrer votre navigateur ou utiliser un bloqueur de traceurs ; ils sont
décrits dans la politique de confidentialité et de cookies de {host}.

**Vos droits**

Vous pouvez demander l’accès aux données qui vous concernent, leur rectification ou leur effacement, vous opposer
à leur traitement ou en demander la limitation, en écrivant à {_contact()}. Si vous estimez que vos droits ne sont
pas respectés, vous pouvez saisir la CNIL ([cnil.fr](https://www.cnil.fr)).

*Dernière mise à jour : {LEGAL_LAST_UPDATE}.*
"""


@st.dialog("Mentions légales", width="large")
def show_legal_notice() -> None:
    st.markdown(build_legal_notice())


@st.dialog("Confidentialité et cookies", width="large")
def show_privacy_notice() -> None:
    st.markdown(build_privacy_notice())


def render_legal_links() -> None:
    """Two discreet buttons for the footer; each opens its text in a dialog."""
    with st.container(key="legal_links"):
        notice_column, privacy_column, _ = st.columns([1, 1.4, 4])
        with notice_column:
            if st.button("Mentions légales", key="legal_notice_open", type="tertiary"):
                show_legal_notice()
        with privacy_column:
            if st.button("Confidentialité et cookies", key="privacy_notice_open", type="tertiary"):
                show_privacy_notice()

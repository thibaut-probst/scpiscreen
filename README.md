# SCPIScreen

A Streamlit prototype for comparing SCPI (Sociétés Civiles de Placement Immobilier) against three investor profiles. The interface is in French; code identifiers and comments are in English.

## Run locally

```bash
uv sync
export SCPISCREEN_ACCESS_CODE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run streamlit run streamlit_app.py
```

Set `SCPISCREEN_ACCESS_CODE` as a server environment variable (or in your hosting provider's secret manager); never commit it. Use a random value of at least 32 characters. The app uses this code to sign a 30-day token stored in a `Secure`, `SameSite=Strict` cookie. Serve the app over HTTPS in production. Changing the access code invalidates existing cookies. Rotate any access code that was previously committed or shared.

`extra-streamlit-components` does not support `HttpOnly` cookies, so the browser-side token is readable by same-origin JavaScript. Streamlit's client IP is also not suitable for enforcing login limits. For a public deployment, put the app behind an identity-aware reverse proxy that issues `HttpOnly` sessions and rate-limits authentication attempts; do not treat the shared access code alone as strong user authentication.

The app reads the `Data`, `Scores` and `Pondérations` tabs from the configured Google Sheet. Set `SHEET_ID` near the top of `scpi_data.py`; the workbook must be readable without signing in (for example, shared as "Anyone with the link"). The app has no sample-data fallback.

## Configure the workbook

The worksheet names are mapped in `WORKSHEETS` in `scpi_data.py`. Keep these tab names or change that mapping to match your workbook.

| Worksheet | Required columns | Purpose |
| --- | --- | --- |
| `Data` | `SCPI` plus the displayed SCPI detail fields | One row per SCPI; provides names and detail data. |
| `Scores` | `Catégorie SCPI`, four `Note ...` profile columns, and the five category score columns | Provides the final pre-calculated scores displayed by the application. |
| `Pondérations` | Row `Catégorie`, row `Critère`, then one row per profile (`Équilibre`, `Stabilité`, `Performance`, `Assurance-vie`, `Nue-Propriété`) | Weights displayed on the « Méthode de classement » page (documentation only; not used for scoring). |

The profile note columns are `Note Equilibre`, `Note Stabilité`, `Note Performance`, and `Note Assurance-vie`. Category columns are `Structure financière`, `Diversification`, `Sécurité locative`, `Maturité`, and `Rentabilité`. Keep the `Catégorie SCPI` names aligned with the `SCPI` names in `Data`; when names differ, the app uses validated row alignment as a fallback.

The `Pondérations` tab can be used to calculate the final notes in the workbook. The application never recalculates profile scores: it displays the final values from `Scores`, and only reads `Pondérations` to explain the method on the « Méthode de classement » page. Rows are located by the label in column A (`Catégorie`, `Critère`, profile names), so row order does not matter; a `Total` column is ignored and recomputed (a total different from 100 % is highlighted).

The full ranking shows the place, SCPI name, profile score, discount, major sectors, and major regions. Select a SCPI name to open its detail sheet. On mobile, an « Affichage » switch toggles between the card list (`Cartes`) and the horizontally scrollable table (`Tableau`). `main_sectors` and `main_regions` are displayed as provided, so separate multiple values with a comma or middle dot.

Numeric values can use a comma decimal separator.

## Notes

- The CSV endpoint reads a worksheet by tab name. Keep the `WORKSHEETS` keys (`data`, `scores`) unchanged when changing tab names.
- Data is cached for 15 minutes. Restarting the app or clearing Streamlit's cache refreshes it immediately.
- This prototype illustrates a comparison method, not investment advice. Replace sample figures with verified source data and review your scoring methodology before publishing.

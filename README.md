# SCPIScreen

A Streamlit prototype for comparing SCPI (Sociétés Civiles de Placement Immobilier) against five investor profiles. The interface is in French; code identifiers and comments are in English.

## Run locally

```bash
uv sync
export SCPISCREEN_ACCESS_CODE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
uv run streamlit run streamlit_app.py
```

Run the command from the project root so that `.streamlit/config.toml` is loaded.

## Access control

- `SCPISCREEN_ACCESS_CODE` is a server environment variable (or a value from your secret manager). Never commit it. It must be at least **16 characters**; the app refuses to start otherwise.
- Access is for a trusted community: one shared code, no individual accounts, no SSO, no reverse proxy.
- **Lockout:** the fifth failed attempt from a client IP bans that IP for **24 hours**. A successful login resets the counter, and failures older than 24 hours no longer count.
- **Unavailable address:** if Streamlit does not expose the client IP, nobody is banned globally. Each browser session gets its own counter (5 failures, then 24 hours), which a page reload resets. Check that `st.context.ip_address` is filled in your deployment.
- Networks that share an IP (mobile carriers, company or box NAT) can block several legitimate members at once. To lift a ban early, remove that client's entry from the `failures` object in the state file.
- **Session cookie:** a random token valid for **1 year**. The server stores only its SHA-256 digest. The cookie cannot be `HttpOnly` without a reverse proxy, so it remains readable by JavaScript.
- **State file:** `.state/access_state.json` (override with `SCPISCREEN_STATE_DIR`). It holds hashed session tokens and ban records. It must stay out of git (`.state/` in `.gitignore`). Deleting it signs everyone out and lifts all bans.
- **Audit log:** `.state/auth.log` records `login_success`, `login_failed`, `ban_created` and `ban_refused`. Client addresses appear only as keyed tags, and the access code is never logged. The file rotates at 1 MB with 6 backups. Restrict its read permissions to the server operator. Keep the retention period short: set a documented duration (for example 90 days) and delete older files with a scheduled task.
- Run a single server process per state file. Locking protects threads only.

## Google Sheet

The app reads the `Data`, `Scores` and `Pondérations` tabs from the configured Google Sheet. Set `SHEET_ID` near the top of `scpi_data.py`.

- **Sharing:** "Anyone with the link", **Viewer** role only. Editors could change the displayed scores, so never grant edit rights through the link.
- The app accepts only CSV responses, and redirects are followed only towards `docs.google.com`.
- There is no sample-data fallback.

### Workbook structure

| Worksheet | Required columns | Purpose |
| --- | --- | --- |
| `Data` | `SCPI` plus the displayed SCPI detail fields | One row per SCPI; provides names and detail data. |
| `Scores` | `Catégorie SCPI`, the five `Note ...` profile columns, and the five category score columns | Final pre-calculated scores displayed by the application. |
| `Pondérations` | Row `Catégorie`, row `Critère`, then one row per profile (`Équilibre`, `Stabilité`, `Performance`, `Assurance-vie`, `Nue-Propriété`) | Weights shown on the « Méthode de classement » page (documentation only; not used for scoring). |

- **Profile note columns:** `Note Equilibre`, `Note Stabilité`, `Note Performance`, `Note Assurance-vie`, `Note Nue-Propriété`.
- **Category columns:** `Structure financière`, `Diversification`, `Sécurité locative`, `Maturité`, `Rentabilité`.
- **Validation:** a missing column, or a score that is empty, not numeric, or outside 0 to 100, shows a warning banner. The affected value counts as 0. Blank spreadsheet rows are ignored.
- Keep `Catégorie SCPI` names aligned with the `SCPI` names in `Data`. When they differ, the app uses validated row alignment as a fallback.
- Numeric values can use a comma decimal separator.
- `main_sectors` and `main_regions` are displayed as provided, so separate multiple values with a comma or middle dot.

The `Pondérations` tab can be used to calculate the final notes in the workbook. The application never recalculates profile scores: it displays the final values from `Scores`, and only reads `Pondérations` to explain the method. Rows are located by the label in column A, so row order does not matter. A `Total` column is ignored and recomputed.

## Ranking page

- The ranking table shows place, SCPI name, profile score, discount, PGA, TRI, major sectors, major regions and the bare-ownership (nue-propriété) indicators.
- The table is rendered in its own frame, which sorts in place without reloading the page. Click a column header to sort; click again to reverse. On narrow screens, « Trier par » sorts the cards, and « Affichage » switches between cards and a horizontally scrollable table.
- Changing the search text or the profile redraws the table, which returns to the default order.
- Click a SCPI name to open its detail sheet inside the frame, then use « Retour au classement ». A link with `?scpi=<id>` opens the sheet on the first load of a session.
- Frame heights are estimated from the number of rows, so the table may show a scrollbar inside the frame or a little empty space underneath.
- The frame follows Streamlit's light or dark theme when `st.context.theme` is available, and the system theme otherwise.

## Fonts

Fonts are self-hosted. Download the variable families and place them in `static/fonts/`:

- DM Sans: https://fonts.google.com/specimen/DM+Sans (source files: https://github.com/google/fonts/tree/main/ofl/dmsans)
- Fraunces: https://fonts.google.com/specimen/Fraunces (source files: https://github.com/google/fonts/tree/main/ofl/fraunces)

Take the variable `.ttf` file from each family (for example `DMSans[opsz,wght].ttf`), rename it, and place it as follows:
static/fonts/DMSans-Variable.ttf
static/fonts/Fraunces-Variable.ttf

Until the files are present, the browser falls back to Georgia and the system sans-serif font. Both families are under the SIL Open Font License.

## Configuration

`.streamlit/config.toml` disables CORS, keeps XSRF protection on, disables usage statistics, hides error details from visitors (they are logged on the server), and serves the `static/` folder for the fonts.

## Limits of this deployment

- Streamlit cannot set security headers such as Content-Security-Policy, HSTS or `frame-ancestors`. These need a reverse proxy, which is out of scope.
- There is no logout button yet. Sessions end after one year, when the state file is deleted, or when the ban or session is revoked in code.
- The frame height is an estimate (see Ranking page).

## Notes

- Data is cached for 15 minutes. Restarting the app clears the cache immediately. The « Vider le cache » menu entry is hidden, because it would affect every visitor.
- This prototype illustrates a comparison method, not investment advice. Replace sample figures with verified source data and review your scoring methodology before publishing.
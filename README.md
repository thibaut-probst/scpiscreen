# SCPIScreen

A Streamlit prototype for comparing SCPI (Sociétés Civiles de Placement Immobilier) against five investor profiles. The interface is in French; code identifiers and comments are in English.

## Run locally

```bash
uv sync
export SCPISCREEN_ACCESS_CODE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
uv run streamlit run streamlit_app.py
```

Run the command from the project root so that `.streamlit/config.toml` is loaded. For development, re-enable file watching: `--server.fileWatcherType auto --server.runOnSave true`.

Deploy with `uv sync --frozen` so that the versions and hashes of `uv.lock` are used as is.

## Code layout

| File | Role |
| --- | --- |
| `streamlit_app.py` | Pages, styles, ranking frame. Calls `require_access()` before anything else. |
| `access_gate.py` | The whole access gate (login form, cookie, bans, HTTPS check) behind one function, `require_access()`. |
| `access_control.py` | Attempt counting, bans, sessions, audit log, state file, command line. |
| `scpi_data.py` | Google Sheet loading, validation, strict join, process-wide cache. |
| `method_page.py` | HTML of the « Méthode de classement » page. |

**Every page must start with `require_access()`.** If you add a `pages/` folder or use `st.navigation`, call it at the top of each page script, after injecting the page styles.

## Access control

- `SCPISCREEN_ACCESS_CODE` is a server environment variable (or a value from your secret manager). Never commit it. It must be at least **16 characters** (leading and trailing spaces are removed); the app refuses to start otherwise. Generate it randomly as shown above.
- Access is for a trusted community: one shared code, no individual accounts, no SSO, no reverse proxy.
- **HTTPS is required.** The session cookie is `Secure`: over plain HTTP it would not be stored. When the browser reaches the app over HTTP from a non-local host, the login form is replaced by an error. `SCPISCREEN_ALLOW_INSECURE_HTTP=1` bypasses this check on a trusted network.
- **Lockout:** each attempt is counted atomically *before* the code is compared, so parallel sessions cannot test more than **5** codes. The fifth wrong attempt bans the client IP for **24 hours**. A successful login resets the counter, and attempts older than 24 hours no longer count.
- **Unavailable address:** if Streamlit does not expose the client IP, nobody is banned globally. Each browser session gets its own counter (5 attempts, then 24 hours), which a page reload resets. Check that `st.context.ip_address` is filled in your deployment, and keep in mind that this address can be spoofed or shared (see Limits).
- **Input:** the code field accepts at most 256 characters; longer input counts as a wrong attempt.
- **Session cookie:** `__Host-scpiscreen_access`, a random token valid for **1 year** (`Secure`, `SameSite=Strict`, path `/`). The server stores only its SHA-256 digest, together with a fingerprint of the access code. The cookie is read on the server (`st.context.cookies`); the third-party component is only used to write it. It cannot be `HttpOnly` without a reverse proxy, so it remains readable by JavaScript.
- **Changing the access code signs everyone out.** Sessions created under another code are rejected and removed. No file needs to be deleted.
- **State directory** `.state/` (override with `SCPISCREEN_STATE_DIR`, mode `0700`). It must stay out of git. It contains:
  - `access_state.json`: hashed session tokens and ban records. Clients are stored under keyed tags, never as clear IP addresses. The number of entries is capped (10 000 each).
  - `server_secret`: a random secret created at the first start (mode `0600`). It keys the client tags and the access-code fingerprint. Deleting it signs everyone out and lifts all bans; the tags in old logs can no longer be matched.
  - `auth.log`: the audit log. It records `login_success`, `login_failed`, `ban_created` and `ban_refused`. Client addresses appear only as keyed tags, and the access code is never logged. Files are created with mode `0600`, rotated every day (UTC) and deleted after **90 days** (`SCPISCREEN_LOG_RETENTION_DAYS`, 1 to 3650).
  - `access.lock`: inter-process lock file.
- **Privacy:** IP addresses are personal data. Mention the logging of connection data (security purpose, retention 90 days) in your legal notice.
- **Lifting a ban early** (use the same `SCPISCREEN_STATE_DIR` as the server):

  ```bash
  uv run python -m access_control bans                      # active bans, with the tag shown in auth.log
  uv run python -m access_control unban --ip 203.0.113.7
  uv run python -m access_control unban --tag 0123456789abcdef
  ```

- State changes are protected by a thread lock and, on POSIX systems, an inter-process file lock. The audit log rotation is not multi-process safe: still run a single server process.

## Google Sheet

The app reads the `Data`, `Scores` and `Pondérations` tabs from the configured Google Sheet. Set `SHEET_ID` near the top of `scpi_data.py`.

- **Sharing:** "Anyone with the link", **Viewer** role only. Editors could change the displayed scores, so never grant edit rights through the link. If the repository is public, the sheet ID is public too: the access code protects the application, not the data.
- The app accepts only CSV responses, and redirects are followed only towards `docs.google.com`.
- There is no sample-data fallback.
- **Cache:** data is kept for 15 minutes and shared by all sessions. If the source fails after that, the last good data stays on screen with a warning stating its age, and the source is not queried again for 60 seconds. Without any previous data, an error is shown. Restarting the app clears the cache.
- **Limit:** at most 300 SCPI are displayed (`MAX_SCPI_ROWS`), because the page embeds one detail sheet per SCPI. A warning appears when rows are left out.

### Workbook structure

| Worksheet | Required columns | Purpose |
| --- | --- | --- |
| `Data` | `SCPI` plus the displayed SCPI detail fields | One row per SCPI; provides names and detail data. |
| `Scores` | `Catégorie SCPI`, the five `Note ...` profile columns, and the five category score columns | Final pre-calculated scores displayed by the application. |
| `Pondérations` | Row `Catégorie`, row `Critère`, then one row per profile (`Équilibre`, `Stabilité`, `Performance`, `Assurance-vie`, `Nue-Propriété`) | Weights shown on the « Méthode de classement » page (documentation only; not used for scoring). |

- **Profile note columns:** `Note Equilibre`, `Note Stabilité`, `Note Performance`, `Note Assurance-vie`, `Note Nue-Propriété`.
- **Category columns:** `Structure financière`, `Diversification`, `Sécurité locative`, `Maturité`, `Rentabilité`.
- **Validation:** a missing column, or a score that is empty, not numeric (`nan` and `inf` included) or outside 0 to 100, shows a warning banner. The affected value counts as 0, everywhere in the application. Blank spreadsheet rows are ignored.
- **Strict join:** a SCPI is matched to its `Scores` row by name only (case, accents, ligatures such as « œ » and apostrophe styles are ignored). There is no positional fallback.
  - The name is read from the `SCPI` column of `Data` by default. If another `Data` column matches more of the `Scores` names (for example a column holding the names exactly as written in `Scores`), it is used automatically, and its values are the names displayed. To force a column, set `DATA_NAME_COLUMN` near the top of `scpi_data.py` to its header.
  - A SCPI with no matching row is shown last, without a place or a score (« — »), and a warning lists it. It is never ranked with invented scores.
  - `Scores` rows without a SCPI in `Data`, and duplicated names in either tab, also raise a warning (the first duplicated `Scores` row is used).
- Numeric values can use a comma decimal separator.
- `main_sectors` and `main_regions` are displayed as provided, so separate multiple values with a comma or middle dot.

The `Pondérations` tab can be used to calculate the final notes in the workbook. The application never recalculates profile scores: it displays the final values from `Scores`, and only reads `Pondérations` to explain the method. Rows are located by the label in column A, so row order does not matter. A `Total` column is ignored and recomputed. Weights are limited to 0 to 100 and non-numeric weights count as 0.

## Ranking page

- The ranking table shows place, SCPI name, profile score, discount, PGA, TRI, major sectors, major regions and the bare-ownership (nue-propriété) indicators.
- The table is rendered in its own frame, which sorts in place without reloading the page. Click a column header to sort; click again to reverse. On narrow screens, « Trier par » sorts the cards, and « Affichage » switches between cards and a horizontally scrollable table.
- The rows and detail sheets of a profile are rendered once per data version and reused; typing in the search box only filters them.
- Changing the search text or the profile redraws the table, which returns to the default order.
- Click a SCPI name to open its detail sheet inside the frame, then use « Retour au classement ». A link with `?scpi=<id>` opens the sheet on the first load of a session (the id is the SCPI name in lower case, without accents, with `-` between words).
- Frame heights are estimated from the number of rows, so the table may show a scrollbar inside the frame or a little empty space underneath.
- The frame follows Streamlit's light or dark theme when `st.context.theme` is available, and the system theme otherwise.
- **Frame security:** the frame runs with scripts and the same origin as the page, so it is not a security boundary. Every value coming from the sheet is HTML-escaped, and the frame document carries a Content-Security-Policy (`<meta>`): only its own script (identified by hash) may run, and nothing can be loaded from or sent to another host. If you change `FRAME_JS`, the hash is recomputed automatically.

## Fonts

Fonts are self-hosted. Download the variable families and place them in `static/fonts/`:

- DM Sans: https://fonts.google.com/specimen/DM+Sans (source files: https://github.com/google/fonts/tree/main/ofl/dmsans)
- Fraunces: https://fonts.google.com/specimen/Fraunces (source files: https://github.com/google/fonts/tree/main/ofl/fraunces)

Take the variable `.ttf` file from each family (for example `DMSans[opsz,wght].ttf`), rename it, and place it as follows:
static/fonts/DMSans-Variable.ttf
static/fonts/Fraunces-Variable.ttf

Until the files are present, the browser falls back to Georgia and the system sans-serif font. Both families are under the SIL Open Font License.

**`static/` is served without authentication.** Put nothing but these fonts in it.

## Configuration

`.streamlit/config.toml` sets the accent colour (`[theme] primaryColor`), keeps XSRF protection on, disables usage statistics, hides error details from visitors (they are logged on the server), hides the developer toolbar, limits WebSocket messages to 20 MB, turns file watching off, and serves the `static/` folder for the fonts. `server.enableCORS` is not set on purpose: Streamlit forces it back to `true` when XSRF protection is on.

If Streamlit reports a message size error with many SCPI, raise `server.maxMessageSize`.

## Limits of this deployment

- Streamlit cannot set security headers such as Content-Security-Policy, HSTS or `frame-ancestors` on the main page. These need a reverse proxy, which is out of scope. The Content-Security-Policy of the ranking frame is the only one in place.
- The client IP reported by Streamlit can be spoofed, and visitors behind the same NAT or hosting platform share one address. A ban can therefore hit several people, and a determined attacker can rotate addresses. The strength of the access code is the real protection.
- There is no logout button yet. Sessions end after one year, when the access code changes, when the server secret or the state file is deleted, or when a session is revoked in code (`revoke_session`).
- The frame height is an estimate (see Ranking page).

## Notes

- This prototype illustrates a comparison method, not investment advice. Replace sample figures with verified source data and review your scoring methodology before publishing.

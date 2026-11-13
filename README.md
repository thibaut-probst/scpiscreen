# SCPIScreen

A Streamlit prototype for comparing SCPI (Sociétés Civiles de Placement Immobilier) against five investor profiles. The interface is in French; code identifiers and comments are in English.

## Run locally

```bash
uv sync
export SCPISCREEN_ACCESS_CODE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
export SCPISCREEN_SERVER_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
uv run streamlit run streamlit_app.py
```

Run the command from the project root so that `.streamlit/config.toml` is loaded. For development, re-enable file watching: `--server.fileWatcherType auto --server.runOnSave true`.

Deploy with `uv sync --frozen` so that the versions and hashes of `uv.lock` are used as is.

Streamlit **1.63 or newer** is required (`st.pills(wrap=True)` keeps the profile pills on several lines on narrow screens).

## Code layout

| File | Role |
| --- | --- |
| `streamlit_app.py` | Pages, styles, ranking frame. Calls `require_access()` before anything else. |
| `access_gate.py` | The whole access gate (login form, cookie, bans, HTTPS check) behind one function, `require_access()`. |
| `access_control.py` | Attempt counting, bans, signed sessions, audit log, state file, command line. |
| `html_frame.py` | The only place that embeds an HTML document in an iframe (wraps the deprecated `components.html`). |
| `scpi_data.py` | Google Sheet loading, validation, strict join, process-wide cache. |
| `method_page.py` | HTML of the « Méthode de classement » page. |

**Every page must start with `require_access()`.** If you add a `pages/` folder or use `st.navigation`, call it at the top of each page script, after injecting the page styles.

## Access control

- `SCPISCREEN_ACCESS_CODE` is a server environment variable (or a value from your secret manager). Never commit it. It must be at least **16 characters** (leading and trailing spaces are removed); the app refuses to start otherwise. Generate it randomly as shown above.
- `SCPISCREEN_SERVER_SECRET` is a second secret, **at least 32 characters**, that signs the session cookies. **Set it on any host whose disk is not persistent** (Streamlit Community Cloud, containers without a volume). Without it, a random secret is stored in `.state/server_secret`: if that file is lost at a restart or a redeployment, every visitor must enter the code again.
- Access is for a trusted community: one shared code, no individual accounts, no SSO, no reverse proxy.
- **HTTPS is required.** The session cookie is `Secure`: over plain HTTP it would not be stored. When the browser reaches the app over HTTP from a non-local host, the login form is replaced by an error. `SCPISCREEN_ALLOW_INSECURE_HTTP=1` bypasses this check on a trusted network.
- **Lockout:** each attempt is counted atomically *before* the code is compared, so parallel sessions cannot test more than **5** codes. The fifth wrong attempt bans the client IP for **24 hours**. A successful login resets the counter, and attempts older than 24 hours no longer count.
- **Unavailable address:** if Streamlit does not expose the client IP, nobody is banned globally. Each browser session gets its own counter (5 attempts, then 24 hours), which a page reload resets. Check that `st.context.ip_address` is filled in your deployment, and keep in mind that this address can be spoofed or shared (see Limits).
- **Optional global limiter:** `SCPISCREEN_GLOBAL_ATTEMPT_LIMIT=40` (for example) refuses new login attempts, all clients together, once 40 attempts were made within the last hour. It is off by default. It compensates for the reload bypass above, but anybody can then lock *new* logins for up to an hour; visitors holding a valid cookie are not affected.
- **Input:** the code field accepts at most 256 characters; longer input counts as a wrong attempt.
- **Session cookie:** `__Host-scpiscreen_access`, valid for **1 year** (`Secure`, `SameSite=Lax`, path `/`). It is a **signed token** (`v1.<expiry>.<nonce>.<signature>`): the server stores nothing about sessions, so they survive restarts and redeployments as long as `SCPISCREEN_SERVER_SECRET` (or `.state/server_secret`) and the access code do not change. It is written by a small script on the page right after a successful login and read on the server (`st.context.cookies`) when a new session starts. It cannot be `HttpOnly` without a reverse proxy, so it remains readable by JavaScript. Safari caps cookies written by JavaScript to about 7 days.
- **Streamlit Community Cloud (and similar hosts): the token goes through the URL.** There, the server never receives cookies written by JavaScript (`st.context.cookies` stays empty on the WebSocket connection), although the browser keeps and sends them: the code was asked at every visit. So, when the server sees no valid cookie, the login page runs a small probe frame. If the browser holds the session cookie, the probe reloads the page **once** with the token in the `sat` query parameter; the server checks it, removes the parameter from the URL at once, grants access and renews the cookie (new one-year expiry). A visitor without cookie sees the login form and no reload. On a host that passes cookies (local run, your own server), the cookie alone is enough and the URL is never used. Consequences: the token appears for a moment in the URL of that one request (browser history keeps the cleaned address; the host's access logs may keep the full one), and an invalid token costs one extra reload per visit until the visitor logs in again.
- **Changing the access code or the server secret signs everyone out.** There is no per-session revocation.
- **State directory** `.state/` (override with `SCPISCREEN_STATE_DIR`, mode `0700`). It must stay out of git (it is in `.gitignore`). It contains:
  - `access_state.json`: ban records and the global counter. Clients are stored under keyed tags, never as clear IP addresses. The number of entries is capped (10 000).
  - `server_secret`: a random secret created at the first start when `SCPISCREEN_SERVER_SECRET` is not set (mode `0600`). It keys the client tags, the access-code fingerprint and the session signatures. Deleting it signs everyone out and lifts all bans.
  - `auth.log`: the audit log. It records `login_success`, `login_failed`, `ban_created`, `ban_refused`, `session_resumed` (a visitor came back with a valid cookie, `via=url` when the token came through the URL), `cookie_absent` (no cookie reached the server; `received=` lists the cookies that did arrive), `cookie_rejected` (a cookie was sent but refused; `reason=bad_signature` means another secret or access code, `expired`, or `malformed`) and `handoff_rejected` (the token of the `sat` parameter was refused, same `reason=`). The same events are also written to stderr, so they show in the logs of hosts without file access (Manage app on Community Cloud). Client addresses appear only as keyed tags, and the access code is never logged. Files are created with mode `0600`, rotated every day (UTC) and deleted after **90 days** (`SCPISCREEN_LOG_RETENTION_DAYS`, 1 to 3650).
  - `access.lock`: inter-process lock file.
- **Diagnostics page:** set `SCPISCREEN_DIAGNOSTICS=1` on the server and open the site with `?diag=1`. It shows which cookies the server received, the state of the session cookie, whether the server secret comes from the environment, and what the browser itself sees and allows (cookie names only, never values). Turn it off after use.
- **If visitors are asked for the code at every visit:** look at `auth.log`. Many `cookie_rejected` mean the secret or the code keeps changing (set `SCPISCREEN_SERVER_SECRET`). Many `cookie_absent` mean the browser does not keep or send the cookie: check in the browser developer tools (Application, Cookies) that `__Host-scpiscreen_access` exists after login, and that the page is served over HTTPS.
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
- The app accepts only CSV responses, and redirects are followed only towards `docs.google.com`. The `Data` and `Scores` tabs are fetched in parallel.
- Column headers are normalized when read: runs of spaces (including non-breaking and doubled spaces) become one space.
- There is no sample-data fallback.
- **Cache:** data is shared by all sessions and considered fresh for 15 minutes. After that, the current data is still served immediately while one background refresh runs, so nobody waits for Google. If that refresh fails, the older data keeps being served **without any warning on the page** (the failure is written to the server log) and the source is queried again after 60 seconds. Without any previous data (first start), an error is shown and the page stops. Restarting the app clears the cache.
- **Limit:** at most 300 SCPI are displayed (`MAX_SCPI_ROWS`), because the page embeds one detail sheet per SCPI. A warning appears when rows are left out.
- Google's CSV endpoint (`gviz`) may return an empty cell where a column mixes numbers and text (for example "NA" in a numeric column). If a "NA" check seems to miss a column, look at how the cell is typed in the sheet.

### Workbook structure

| Worksheet | Required columns | Purpose |
| --- | --- | --- |
| `Data` | `SCPI` plus the displayed SCPI detail fields | One row per SCPI; provides names and detail data. |
| `Scores` | `Catégorie SCPI`, the five `Note ...` profile columns, and the five category score columns | Final pre-calculated scores displayed by the application. |
| `Pondérations` | Row `Catégorie`, row `Critère`, then one row per profile (`Équilibre`, `Stabilité`, `Performance`, `Assurance-vie`, `Nue-Propriété`) | Weights shown on the « Méthode de classement » page (documentation only; not used for scoring). |

- **Profile note columns:** `Note Equilibre`, `Note Stabilité`, `Note Performance`, `Note Assurance-vie`, `Note Nue-Propriété`.
- **Category columns:** `Structure financière`, `Diversification`, `Sécurité locative`, `Maturité`, `Rentabilité`.
- **Validation:** a missing column, or a score that is empty, not numeric (`nan` and `inf` included) or outside 0 to 100, shows a warning banner. Blank spreadsheet rows are ignored.
  - An invalid **profile** score means the SCPI is **not ranked for that profile**: it comes last, without a place, with « — » as its score. No score is invented.
  - An invalid **category** score is shown as « —/100 » in the detail sheet.
- **Missing data:** a column that is « NA » (or blank, for the weighted criteria) for more than 50 % of the SCPI shows a warning. Weighted criteria are looked up in `Scores` first, then in `Data`; every other column of those tabs is checked for explicit « NA » cells. « NA »-like values (`NA`, `n/a`, `-`, `—`…) are displayed as « — » everywhere.
- **Strict join:** a SCPI is matched to its `Scores` row by name only (case, accents, ligatures such as « œ » and apostrophe styles are ignored). There is no positional fallback.
  - The name is read from the `SCPI` column of `Data` by default. If another `Data` column matches more of the `Scores` names (for example a column holding the names exactly as written in `Scores`), it is used automatically, and its values are the names displayed. To force a column, set `DATA_NAME_COLUMN` near the top of `scpi_data.py` to its header.
  - A SCPI with no matching row is shown last, without a place or a score (« — »), and a warning lists it.
  - `Scores` rows without a SCPI in `Data`, and duplicated names in either tab, also raise a warning (the first duplicated `Scores` row is used).
- Numeric values can use a comma decimal separator, and any kind of space as thousands separator.
- `main_sectors` and `main_regions` are displayed as provided, so separate multiple values with a comma or middle dot.

The `Pondérations` tab can be used to calculate the final notes in the workbook. The application never recalculates profile scores: it displays the final values from `Scores`, and only reads `Pondérations` to explain the method. Nothing checks that the two agree: if you change the weights, update the formulas of `Scores` too. Rows are located by the label in column A, so row order does not matter. A `Total` column is ignored and recomputed. Weights are limited to 0 to 100 and non-numeric weights count as 0.

## Ranking page

- The ranking table shows place, SCPI name, profile score, discount, PGA, TRI, major sectors, major regions and the bare-ownership (nue-propriété) indicators.
- The table is rendered in **one** frame that sorts and filters in place without reloading the page. Click (or press Enter on) a column header to sort; click again to reverse. Missing values always come last. On narrow screens, « Trier par » sorts the cards, and the « Cartes / Tableau » switch inside the frame toggles between cards and a horizontally scrollable table.
- **Search** is done inside the frame, in the browser: accents and case are ignored, nothing is sent to the server, and the current sort order is kept.
- The rows and detail sheets of a profile are rendered once per data version and shared by all sessions. Changing the profile redraws the table, which returns to the default order.
- Click a SCPI name to open its detail sheet inside the frame, then use « Retour au classement » (the list scrolls back to the row). A link with `?scpi=<id>` opens the sheet on the first load of a session (the id is the SCPI name in lower case, without accents, with `-` between words); an unknown id is reported once.
- The frame resizes itself to its content (it has the same origin as the page). `FRAME_INITIAL_HEIGHT_PX` is only the height of the first paint, and the height kept if the browser forbids the resize.
- The frame follows Streamlit's light or dark theme when `st.context.theme` is available, and the system theme otherwise.
- **Frame security:** the frame runs with scripts and the same origin as the page, so it is not a security boundary. Every value coming from the sheet is HTML-escaped, and the frame document carries a Content-Security-Policy (`<meta>`): only its own script (identified by hash) may run, and nothing can be loaded from or sent to another host. If you change `FRAME_JS`, the hash is recomputed automatically.
- `st.components.v1.html` is deprecated: all frames go through `html_frame.render_html_frame()`, which falls back to `st.iframe`. After a Streamlit upgrade, check that the session cookie is still written and that the ranking frame still resizes.

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

With file watching off, a change to a module (`scpi_data.py`, `access_gate.py`…) is only taken into account after the server restarts.

If Streamlit reports a message size error with many SCPI, raise `server.maxMessageSize`.

## Limits of this deployment

- Streamlit cannot set security headers such as Content-Security-Policy, HSTS or `frame-ancestors` on the main page. These need a reverse proxy, which is out of scope. The Content-Security-Policy of the ranking frame is the only one in place.
- The client IP reported by Streamlit can be spoofed, and visitors behind the same NAT or hosting platform share one address. A ban can therefore hit several people, and a determined attacker can rotate addresses. The strength of the access code is the real protection.
- There is no logout button yet. Sessions end after one year, or when the access code or the server secret changes.

## Notes

- This prototype illustrates a comparison method, not investment advice. Replace sample figures with verified source data and review your scoring methodology before publishing.

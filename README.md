# C1C-Matchmaker v1.0.1

A Discord bot that helps recruiters place members into the right C1C clan, lets members browse open spots, and posts configurable welcome messages. It also exposes a tiny HTTP service (health checks + emoji-pad proxy) and has light housekeeping jobs.

## Contents

* `bot_clanmatch_prefix.py` — main bot (recruiter/member panels, search, profiles, health, cache, web server, daily summaries, cleanup, watchdog).
* `welcome.py` — self-contained Cog for templated welcome messages powered by a spreadsheet tab.

> The bot uses **prefix commands** (e.g., `!clanmatch`). Intents: `message_content` must be enabled.

---

## Features

* **Recruiter panel**: `!clanmatch` opens a private filter panel (CB/Hydra/Chimera diff, CvC Yes/No, Siege Yes/No, Playstyle, Roster). Results paginate.
* **Member panel**: `!clansearch` opens a slim “browse open clans” panel with three view modes (Lite / Entry Criteria / Profile) + paging.
* **Clan profiles**: `!clan <tag or name>` shows a full profile; react with 💡 to flip to Entry Criteria (and back).
* **Welcome posts**: `!welcome <TAG> [@user]` posts a templated embed to the clan’s channel and a mini notice to “general”. Templates live in a sheet tab; `{MENTION}/{CLAN}` etc. placeholders supported; custom emoji tokens `{EMOJI:…}` resolve to server emojis.
* **Daily recruiters summary**: once per day to a configured thread, with optional role mentions.
* **Sheets caching + scheduled refresh**: cache is warmed and refreshed 3×/day (configurable).
* **Housekeeping**: scheduled cleanup of old bot messages in specified threads/channels.
* **Health + tiny web server**: `/health`, `/ready`, `/healthz` and `/emoji-pad` (proxy that pads/centers emoji images).
* **Self-recovery watchdog**: restarts the process if the gateway is disconnected too long or appears zombie-ish.

---

## Commands

### Recruiters / Members

* `!clanmatch` — open recruiter panel (owner-locked).
* `!clansearch` — open member panel (owner-locked).
* `!clan <tag or name>` — show clan profile; 💡 to flip to Entry Criteria.
* `!help [topic]` — overview or details: `clanmatch`, `clansearch`, `clan`, `welcome`, `reload`, `health`, `ping`, `welcome-*`.

### Welcome module

* `!welcome <TAG> [@user]` — post a welcome to the configured channel for TAG; @user is optional (or reply to their message).
* `!welcome-refresh` — reload templates from the sheet.
* `!welcome-on` / `!welcome-off` / `!welcome-status` — runtime toggle and status.

### Admin / Maintenance

* `!reload` — clear the sheet cache (next query refetches).
* `!health` / `!status` — show latency, uptime, sheet status, last-event age.
* `!ping` — simple alive check.
* `!mmhealth` — minimal OK probe text (for alerts/dashboards).

> Panel owner gates, role gates, and admin gates are enforced. See **Permissions** below.

---

## Google Sheets schema

All data is read-only. The bot expects:

### Main worksheet (default name: `bot_info`)

Row/column expectations (0-based indices are in code; human view below):

* **A:** Global Rank
* **B:** Clan Name
* **C:** Tag
* **D:** Level
* **E:** Spots (text with a number, e.g., “3 open”)
* **F:** Progression (free text)
* **G:** Clan Lead
* **H:** Deputies
* **I–L:** CvC Tier / CvC Wins / Siege Tier / Siege Wins
* **M–O:** Clan Boss range / Hydra range / Chimera range (free text ranges that include tokens like “NM”, “UNM”, “Hard”, “Brutal”)
* **P–T (filters):** CB / Hydra / Chimera (free text containing the same tokens), CvC (exact “1/0”), Siege (exact “1/0”)
* **U (style):** Playstyle tokens (“Stress-Free”, “Casual”, “Semi-Competitive”, “Competitive”; case/spacing tolerant)
* **AC/AD/AE/AF:** Reserved spots / Comments / Additional requirements / Inactives (a number)

Recruiter summary table (for the daily post) somewhere near the top 80 rows with a header row that includes **“open spots”**, **“inactives”**, **“reserved spots”**, followed by lines labeled (anywhere in the row) like:
`overall`, `top10`, `top5`, `Elite End Game`, `Early End Game`, `Late Game`, `Mid Game`, `Early Game`, `Beginners`.
The bot pulls integer values from the three relevant columns.

### Welcome templates worksheet (default tab: `WelcomeTemplates`)

Required columns (header names must match):

```
TAG, TARGET_CHANNEL_ID, TITLE, BODY, FOOTER, CREST_URL, PING_USER, ACTIVE,
CLAN, CLANLEAD, DEPUTIES, GENERAL_NOTICE
```

* Special row **TAG=C1C** acts as the **default template**. For any clan row, empty `TITLE/BODY/FOOTER` are filled from the C1C row.
* Placeholders: `{MENTION}`, `{USERNAME}`, `{CLAN}`, `{CLANTAG}`, `{GUILD}`, `{NOW}` (Europe/Vienna by default), `{INVITER}`, `{CLANLEAD}`, `{DEPUTIES}`.
* Emoji tokens: `{EMOJI:<id-or-name>}` resolve to server emojis (by numeric ID or sanitized name).
* `TARGET_CHANNEL_ID` must be a **channel ID** (string of digits).
* `PING_USER`: `Y` to ping the mentioned/new member at the top of the message.

---

## Environment variables

### Discord / general

* `DISCORD_TOKEN` — your bot token (required).
* `STRICT_PROBE` — `1` to make `/` and `/ready` return deep health (200/206/503). Default `0` (always 200; use `/healthz` for deep).
* `PORT` — HTTP server port (platform-provided on Render/Heroku; defaults to `10000`).

### Google Sheets

* `GSPREAD_CREDENTIALS` — **JSON string** of a Service Account (read-only scope is used).
* `GOOGLE_SHEET_ID` — spreadsheet ID (the long key in the URL).
* `WORKSHEET_NAME` — main tab name (default `bot_info`).
* `WELCOME_SHEET_TAB` — welcome templates tab name (default `WelcomeTemplates`).
* `SHEETS_CACHE_TTL_SEC` — cache TTL for `bot_info` tab (default **8h**). Use `!reload` to force a refresh.
* `REFRESH_TIMES` — local times to refresh the cache, comma-separated `HH:MM` (default `02:00,10:00,18:00`).
* `TIMEZONE` — IANA timezone for `REFRESH_TIMES` (default `Europe/Vienna`).

### Recruiters daily summary + mentions

* `RECRUITERS_THREAD_ID` — thread/channel ID to post the daily summary (required to enable the job).
* `ROLE_ID_RECRUITMENT_COORDINATOR` — role ID to mention (optional).
* `ROLE_ID_RECRUITMENT_SCOUT` — role ID to mention (optional).
* `POST_TIME_UTC` — set inside the file (default **17:30 UTC**). Change in code if needed.

### Panels / threading

* `PANEL_THREAD_MODE` — `same` (default) posts the recruiter panel in the invoking channel; `fixed` forces a single thread.
* `PANEL_FIXED_THREAD_ID` — when `fixed`, the destination **thread ID**.
* `PANEL_PARENT_CHANNEL_ID` — not used in current code (reserved for future).
* `PANEL_THREAD_ARCHIVE_MIN` — minutes to keep the fixed thread unarchived (default 10080 / 7 days).
* `SEARCH_RESULTS_SOFT_CAP` — max results shown per search (default 25).
* `SHOW_TAG_IN_CLASSIC` — `1` to show tag thumbnails on recruiter results (default off to save space).

### Emoji pad / thumbnails

* `PUBLIC_BASE_URL` or `RENDER_EXTERNAL_URL` — public base URL for the bot’s web server (for `/emoji-pad` links).
* `EMOJI_MAX_BYTES` — max downloaded image size (default 2 MB).
* `EMOJI_PAD_SIZE` — canvas size in px (default 256).
* `EMOJI_PAD_BOX` — glyph fill ratio 0.2–0.95 (default 0.85).
* `TAG_BADGE_PX` / `TAG_BADGE_BOX` — size/fill for the attachment thumbnails (default 128 / 0.90).
* `STRICT_EMOJI_PROXY` — `1` to **require** proxy URLs; `0` allows direct CDN thumbnails when proxy is unavailable.

### Roles / gating

* `RECRUITER_ROLE_IDS` — comma/space-separated list; can use **either** these or Admin perms for `!clanmatch`.
* `LEAD_ROLE_IDS` — allow health/reload/ping.
* `ADMIN_ROLE_IDS` — extra admin-ish roles (also allow health/reload/ping).
* `WELCOME_ALLOWED_ROLES` — who can use the welcome commands (if empty → anyone).
* `WELCOME_GENERAL_CHANNEL_ID` — channel to post the general notice (optional).
* `WELCOME_ENABLED` — `Y` (default) or `N` to start the module disabled.

> **Note:** `LOG_CHANNEL_ID` is currently set inline in `bot_clanmatch_prefix.py`. Consider moving it to an env var for flexibility.

### Cleanup

* `CLEANUP_THREAD_IDS` — comma/space-separated IDs to purge old bot messages from.
* `CLEANUP_EVERY_HOURS` — run interval (default 24h).
* `CLEANUP_AGE_HOURS` — delete messages older than this (default 24h).

### Watchdog

* `WATCHDOG_CHECK_SEC` — watchdog poll period (default 60s).
* `WATCHDOG_MAX_DISCONNECT_SEC` — max allowed disconnect before forced restart (default 600s).

---

## Install & run

1. **Python** 3.10+ recommended (discord.py 2.x).

2. Install deps:

   ```bash
   pip install discord.py gspread google-auth aiohttp pillow
   ```

3. Place files:

   ```
   bot_clanmatch_prefix.py
   welcome.py
   ```

   > If your file is named differently, either rename to `welcome.py` or adjust `from welcome import Welcome`.

4. Set environment variables (see examples above). Typical minimal `.env`:

   ```env
   DISCORD_TOKEN=xxxxx
   GSPREAD_CREDENTIALS={"type":"service_account",...}
   GOOGLE_SHEET_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   WORKSHEET_NAME=bot_info
   WELCOME_SHEET_TAB=WelcomeTemplates
   PUBLIC_BASE_URL=https://your-app.onrender.com
   RECRUITERS_THREAD_ID=000000000000000000
   RECRUITER_ROLE_IDS=111111111111111111,222222222222222222
   LEAD_ROLE_IDS=333333333333333333
   ```

5. **Run**:

   ```bash
   python bot_clanmatch_prefix.py
   ```

   The bot logs in, starts the web server, daily jobs, the Sheets refresh scheduler, adds the welcome cog, and warms templates.

---

## Permissions (Discord)

Recommended bot permissions:

* Read Messages/View Channels, Send Messages, Embed Links, Attach Files, Add Reactions.
* Read Message History.
* Manage Messages (for cleanup + panel housekeeping).
* Manage Threads (if using a fixed thread + unarchive).
* Use Slash Commands permission can be off; this bot is prefix-based.

Enable **Message Content Intent** in the developer portal.

---

## How matching works (filters)

* **Difficulty tokens** are normalized: `Easy/Normal/Hard/Brutal/NM/UNM`. Ranges in the sheet are free text but must include those tokens.
* **CvC/Siege** are exact `1` or `0` matches.
* **Playstyle** accepted tokens: `Stress-Free`, `Casual`, `Semi-Competitive`, `Competitive` (spacing and hyphen variants allowed).
* **Roster**: “Open Only” (Spots > 0), “Inactives Only” (Inactives > 0), “Full Only” (Spots ≤ 0), or “All”.

---

## HTTP endpoints

* `/` and `/ready`: 200 (or deep check if `STRICT_PROBE=1`).
* `/healthz`: deep JSON health (200 when connected; 503 when disconnected; 206 if “zombie-ish”).
* `/emoji-pad?u=<cdn-url>&s=<size>&box=<0..1>&v=<id>`: trims transparent borders, centers glyph on a square PNG. Host allowlist: `cdn.discordapp.com`, `media.discordapp.net`.

---

## Troubleshooting

* **Nothing appears on `!clanmatch` clicks**: Only the opener can use their panel (owner-locked).
* **No search results**: Too many filters; try fewer. Also verify tokens in the sheet include the canonical difficulty keywords.
* **Welcome says “No target channel configured”**: `TARGET_CHANNEL_ID` must be a numeric channel ID in the templates tab.
* **429 from Sheets**: The bot caches rows (TTL default 8h) and has a scheduled refresh. Avoid hammering `!reload`. If using Render, stagger multiple instances or increase TTL.
* **Images missing**: set `PUBLIC_BASE_URL` (or `RENDER_EXTERNAL_URL`) so padded emoji URLs resolve publicly. Set `STRICT_EMOJI_PROXY=0` to allow direct CDN thumbnails when proxying fails.
* **Fixed recruiter thread**: ensure `PANEL_THREAD_MODE=fixed` and `PANEL_FIXED_THREAD_ID` is a valid **thread** the bot can write to. The bot will unarchive it as needed.

---

## Notes / design choices

* The welcome module **merges** empty `TITLE/BODY/FOOTER` from the `C1C` default row, but routing (target channel) comes from the clan’s own row.
* The reaction flip uses an in-memory index (`REACT_INDEX`); deleting the message drops its state.
* The watchdog exits the process on hard failures; your host should restart the process.
* `LOG_CHANNEL_ID` is currently a constant in code. Consider making it an env var.

---

## License

Internal project. 

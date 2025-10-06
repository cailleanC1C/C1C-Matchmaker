# Architecture Map — Matchmaker Snapshot

## Current Flow
1. **Intake (prefix commands)** — `!clanmatch` / `!clansearch` create a `ClanMatchView` (discord UI view) bound to the opener. No dedicated intake module; validation handled inline.
2. **Data access** — `get_rows()` lazily connects to the `bot_info` worksheet via gspread and caches the full table in memory. All matching, summaries, and profiles reuse this cache.
3. **Scoring & filtering** — `ClanMatchView.search` filters cached rows based on UI state (CB/Hydra/Chimera/CvC/Siege/Playstyle + roster availability). Manual overrides & waitlists are not represented in this code drop.
4. **Rendering** — Multiple embed builders render recruiter vs member cards, entry criteria, and profiles. Reaction handlers (`REACT_INDEX`) swap between profile and entry embeds.
5. **Notifications** — Daily recruiter summary posts into a configured thread. Recruiter/member panels reply in-channel; no dedicated recruiter lounge broadcast exists yet.
6. **Roles / placement** — No automated role assignment here; presumed to remain manual or out of scope for this snapshot.
7. **Logging & health** — Console logging plus optional thread notifications. HTTP health server exposes `/healthz` via aiohttp. Welcome cog logs to a fixed channel.

## Target Stabilization Points
- **Intake layer** — Extract panel creation and validation into `matchmaker/intake.py` (mirroring Reminder) to centralize duplicate guards and DM fallbacks.
- **Sheets adapter** — Introduce an async wrapper service that owns the gspread client, enforces schema, and exposes typed accessors (brackets, open spots, recruiters summary).
- **Matcher/service boundary** — Separate filtering/scoring logic (including bracket caps, waitlists, manual overrides) from Discord UI, returning structured placement candidates.
- **Notifications** — Create dedicated router for recruiter lounge posts, candidate DM/thread confirmation, and optional Recruitment Needs sync.
- **Roles** — Encapsulate role removal/add flows with retry & audit hooks once automated placement is added.
- **Observability** — Move logging config + correlation IDs into a `common/logging.py` helper shared by matchmaker and welcome modules.

# Threat Assessment

## Assets & Trust Boundaries
- **Discord guild data** — Messages, channel IDs, member roles accessed via bot token with elevated permissions.
- **Recruitment Sheets** — Google Sheets service account credentials supply roster, requirements, and welcome templates.
- **Matchmaker decisions** — Placement recommendations and summaries influence guild staffing; errors impact clan balance.
- **Webhook / HTTP surface** — aiohttp server exposes health and emoji proxy endpoints reachable from the internet.

## Key Risks
1. **Service account leakage** — `GSPREAD_CREDENTIALS` loaded from env; ensure secrets are not logged (currently safe) and restrict scope to read-only.
2. **Permission abuse** — Prefix commands rely on role ID gates; missing checks for future override/move flows could let members escalate. Monitor when adding those commands.
3. **PII in logs** — Recruiter summaries and welcome logs include clan/member mentions; log channel must stay private and redact sensitive sheet columns before printing.
4. **Emoji proxy SSRF** — Handler restricts hosts to Discord CDN and caps payload size, mitigating SSRF/DoS.
5. **Zombie watchdog restart** — `_watchdog` calls `sys.exit(1)` on prolonged disconnect; ensure hosting platform restarts process and that audit log entries precede exit.

## Recommended Mitigations
- Store sheet access + welcome template fetches behind a dedicated adapter that strips unused columns before logging to reduce PII exposure.
- Expand permission decorators once override/move commands are added (e.g., `@commands.has_any_role(*ADMIN_ROLE_IDS)`).
- Add structured logging (guild id, message id) with log levels to simplify incident tracing without exposing raw application text.
- Harden HTTP startup (F-03) so monitors never hit a silent failure mode.

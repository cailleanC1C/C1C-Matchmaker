# Test Plan — Proposed Scenarios

1. **Panel happy path** — `!clanmatch` as recruiter; apply CB + roster filters; verify embeds paginate, cap note shown when results > soft cap.
2. **DM-off fallback** — Attempt `!clansearch` in guild where member has DMs disabled; ensure all responses stay in-channel without leaking to others.
3. **Duplicate intake guard** — Open `!clanmatch` twice rapidly; confirm second invocation edits the existing panel and original command message is deleted.
4. **Full clan fallback** — Force sheet row with 0 spots; search with roster `open`; verify the clan is excluded. Flip to `full` roster and ensure it reappears.
5. **Manual override / waitlist** — (Future) Simulate recruiter override once matcher service exists; ensure history persists and notifications adjust.
6. **Daily recruiter summary** — Manually trigger `daily_recruiters_update` after cache warm; confirm embed values align with sheet summary and thread mention order is correct.
7. **Welcome command** — Run `!welcome TAG @user`; confirm template merge, emoji substitution, general notice, and log channel entry.
8. **Role assignment failure** — (Future) Simulate add-role 403; verify compensating actions (log + rollback) once automated placement lands.
9. **Health endpoints** — Curl `/`, `/ready`, `/healthz` with `STRICT_PROBE` on/off; confirm status codes adjust and emoji proxy enforces host allowlist.
10. **Watchdog zombie detection** — Pause gateway events >10m with high latency; expect watchdog-triggered restart and audit log message.

# C1C Matchmaker — Code Review

## Executive Summary
**Status: Yellow** — Core commands rely on Google Sheets calls that run synchronously on the gateway thread and can freeze the bot under modest latency. Addressing the blocking I/O and one misconfiguration hotspot will improve robustness before further carve-out work.

## Findings

### High Severity

#### F-01 · Robustness · Google Sheets fetches block the event loop
**Location:** `bot_clanmatch_prefix.py:123–144`, `bot_clanmatch_prefix.py:1237–1448`, `bot_clanmatch_prefix.py:1532–1557`, `bot_clanmatch_prefix.py:1942`

> `rows = get_rows(False)`

**Issue.** `get_rows()` and helpers call gspread’s `worksheet.get_all_values()`, which uses synchronous `requests`. The calls happen directly inside interaction handlers (`ClanMatchView.search` / `_maybe_refresh`), the daily poster, and the `!clan` command. When Sheets is slow (common at peak), the entire Discord event loop blocks for several seconds, delaying heartbeats and triggering disconnects/zombie watchdog exits. This violates the async hygiene goal in the brief.

**Recommendation.** Offload the blocking sheets work onto worker threads using `asyncio.to_thread(...)` at every async call site so the gateway loop stays responsive.

```diff
@@
-        try:
-            rows = get_rows(False)
+        try:
+            rows = await asyncio.to_thread(get_rows, False)
@@
-            rows = get_rows(False)
+            rows = await asyncio.to_thread(get_rows, False)
@@
-        embed = build_recruiters_summary_embed(getattr(thread, "guild", None))
+        embed = await asyncio.to_thread(
+            build_recruiters_summary_embed, getattr(thread, "guild", None)
+        )
@@
-        row = find_clan_row(query)
+        row = await asyncio.to_thread(find_clan_row, query)
```

**Verify.**
- Trigger `!clanmatch` and flip filters while throttling Sheets latency; confirm interactions remain responsive.
- Let the daily poster fire (or run manually) and ensure the summary embed still posts.
- Call `!clan <tag>` and confirm results unchanged.

### Medium Severity

#### F-02 · DX · Welcome log channel is hard-coded
**Location:** `bot_clanmatch_prefix.py:2498–2523`

> `LOG_CHANNEL_ID = 1415330837968191629`

**Issue.** The welcome cog is wired to a fixed channel ID. On any deployment outside the original guild this resolves to an inaccessible channel, so welcome command executions lose their audit log and the cog silently drops messages. It contradicts the README note that `LOG_CHANNEL_ID` should be configurable.

**Recommendation.** Source the welcome log channel from environment (with a safe default) instead of a baked-in ID.

```diff
-LOG_CHANNEL_ID = 1415330837968191629
+LOG_CHANNEL_ID = int(
+    os.getenv("WELCOME_LOG_CHANNEL_ID", os.getenv("LOG_CHANNEL_ID", "0")) or "0"
+)
```

**Verify.**
- Set `WELCOME_LOG_CHANNEL_ID` in `.env`, restart, run `!welcome …`, and confirm log messages land in the chosen channel.

#### F-03 · Robustness · Web server failures are swallowed at startup
**Location:** `bot_clanmatch_prefix.py:2529–2537`

> `asyncio.create_task(start_webserver())`

**Issue.** The health-check server is started via `create_task` and the task is never awaited. If port binding fails (e.g., port already in use) the exception becomes an unobserved task error; the bot keeps running without `/healthz`, defeating watchdog/Render probes.

**Recommendation.** Await the startup coroutine so failures abort boot immediately.

```diff
-        asyncio.create_task(start_webserver())
+        await start_webserver()
```

**Verify.**
- Run with `PORT` set to an already-bound port; confirm the process now fails fast instead of running without HTTP endpoints.

## Notes
- See `REVIEW/FINDINGS.md` for a numbered index and `REVIEW/TODOS.md` for follow-up tasks.
- Architecture, threats, and test guidance are documented in the accompanying artifacts.

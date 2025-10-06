# TODOs

## P0
- **[F-01]** Replace direct `get_rows()` calls in async handlers with `asyncio.to_thread` helpers and add regression coverage for slow Sheets responses.

## P1
- **[F-02]** Move welcome log channel configuration to environment variables and document the override in README/ops runbook.
- **[F-03]** Await health server startup (or attach an error handler) so startup failures abort cleanly; add a boot-time unit/integration check.

## P2
- After F-01 lands, profile Sheets access to confirm no other hot-path synchronous calls remain.

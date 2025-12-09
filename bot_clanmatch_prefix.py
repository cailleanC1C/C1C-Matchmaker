<the entire file you pasted above remains unchanged until this point>


async def start_webserver():
    """
    Starts aiohttp webserver WITHOUT creating any global/shared ClientSession.
    This avoids the unclosed-connector boot failure on Render.
    """
    global _WEB_RUNNER

    app = web.Application()

    # ❌ Removed: app["session"] = ClientSession()
    # We do NOT create any ClientSession here.
    # emoji_pad_handler uses its own per-request ClientSession, which is safe.

    # Routes
    if STRICT_PROBE:
        app.router.add_get("/", _health_json)
        app.router.add_get("/ready", _health_json)
        app.router.add_get("/health", _health_json)
    else:
        app.router.add_get("/", _health_json_ok_always)
        app.router.add_get("/ready", _health_json_ok_always)
        app.router.add_get("/health", _health_json_ok_always)

    app.router.add_get("/healthz", _health_json)
    app.router.add_get("/emoji-pad", emoji_pad_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    _WEB_RUNNER = runner

    port = int(os.environ.get("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print(f"[keepalive] webserver running on :{port}", flush=True)


async def stop_webserver():
    """
    Properly shuts down the webserver.
    Ensures cleanup hooks fire (even though no shared session exists anymore).
    """
    global _WEB_RUNNER
    runner = _WEB_RUNNER
    _WEB_RUNNER = None

    if not runner:
        return

    try:
        await runner.cleanup()
        print("[keepalive] webserver shut down cleanly", flush=True)
    except Exception as e:
        print(f"[keepalive] webserver shutdown error: {e}", flush=True)


# --------------- Integration of welcome.py for welcome messages --------------------------
from welcome import Welcome  # or: from modules.welcome import Welcome

WELCOME_ALLOWED_ROLES = {int(x) for x in os.getenv("WELCOME_ALLOWED_ROLES","").split(",") if x.strip().isdigit()}
WELCOME_GENERAL_CHANNEL_ID = int(os.getenv("WELCOME_GENERAL_CHANNEL_ID","0")) or None
WELCOME_ENABLED = os.getenv("WELCOME_ENABLED","Y").upper() != "N"
LOG_CHANNEL_ID = 1415330837968191629
C1C_FOOTER_EMOJI_ID = int(os.getenv("C1C_FOOTER_EMOJI_ID","0")) or None

def get_welcome_rows():
    """Return list[dict] from the WelcomeTemplates tab in the same spreadsheet."""
    tab = os.getenv("WELCOME_SHEET_TAB", "WelcomeTemplates")
    creds = Credentials.from_service_account_info(json.loads(CREDS_JSON), scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(tab)
    return ws.get_all_records()

welcome_cog = Welcome(
    bot,
    get_rows=get_welcome_rows,
    log_channel_id=LOG_CHANNEL_ID,
    general_channel_id=WELCOME_GENERAL_CHANNEL_ID,
    allowed_role_ids=WELCOME_ALLOWED_ROLES,
    c1c_footer_emoji_id=C1C_FOOTER_EMOJI_ID,
    enabled_default=WELCOME_ENABLED,
)

# Flags to ensure we only add/prime once
_WELCOME_ADDED = False
_WELCOME_PRIMED = False

# ------------------- Boot both -------------------

async def _maybe_restart(reason: str):
    try:
        log.warning(f"[WATCHDOG] Restarting: {reason}")
    except NameError:
        print(f"[WATCHDOG] Restarting: {reason}")
    # Try to shut down the webserver cleanly first
    try:
        await stop_webserver()
    except Exception as e:
        print(f"[WATCHDOG] webserver shutdown error: {type(e).__name__}: {e}", flush=True)
    try:
        await bot.close()
    finally:
        sys.exit(1)

async def main():
    try:
        # Start the tiny webserver in the background
        asyncio.create_task(start_webserver())

        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if not token or len(token) < 50:
            raise RuntimeError("Missing/short DISCORD_TOKEN.")
        print("[boot] starting discord bot…", flush=True)
        await bot.start(token)
    except Exception as e:
        print("[boot] FATAL:", e, flush=True)
        traceback.print_exc()
        raise
    finally:
        try:
            await stop_webserver()
        except Exception as e:
            print(f"[boot] webserver shutdown error: {type(e).__name__}: {e}", flush=True)

if __name__ == "__main__":
    asyncio.run(main())

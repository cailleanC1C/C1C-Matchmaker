# welcome.py 
# C1C Matchmaker — Welcome Module v1.0
# Drop-in Cog. No external deps beyond discord.py.

import asyncio
import re
from datetime import datetime, timezone
from typing import Callable, Dict, Any, List, Optional

import discord
from discord.ext import commands

# ---------------- Logging ----------------
def _fmt_kv(**kv) -> str:
    return " ".join(f"{k}={v}" for k, v in kv.items() if v is not None)

async def log_to_channel(bot: commands.Bot, log_channel_id: int, level: str, msg: str, **kv):
    prefix = f"[c1c-matchmaker/welcome/{level}]"
    line = f"{prefix} {msg}"
    if kv:
        line += f" • {_fmt_kv(**kv)}"
    print(line, flush=True)
    try:
        ch = bot.get_channel(log_channel_id) or await bot.fetch_channel(log_channel_id)
        if ch:
            await ch.send(line)
    except Exception:
        pass  # never recurse on log failures

# ---------------- Emoji handling ----------------

_EMOJI_TOKEN = re.compile(r"{EMOJI:([^}]+)}")

def _sanitize_emoji_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", (name or "").lower())

def _resolve_emoji(guild: discord.Guild, token: str) -> str:
    token = (token or "").strip()
    # If numeric, treat as emoji ID
    if token.isdigit():
        for e in guild.emojis:
            if str(e.id) == token:
                return f"<{'a' if e.animated else ''}:{e.name}:{e.id}>"
        return token
    # Otherwise, match by (sanitized) name
    want = _sanitize_emoji_name(token)
    for e in guild.emojis:
        if e.name.lower() == want:
            return f"<{'a' if e.animated else ''}:{e.name}:{e.id}>"
    return token

def _replace_emoji_tokens(text: str, guild: discord.Guild) -> str:
    return _EMOJI_TOKEN.sub(lambda m: _resolve_emoji(guild, m.group(1)), text or "")

def _emoji_cdn_url_from_id(guild: discord.Guild, emoji_id: int) -> Optional[str]:
    try:
        for e in guild.emojis:
            if e.id == emoji_id:
                ext = "gif" if e.animated else "png"
                return f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
        # If not found in cache, assume png (Discord will serve it if valid)
        return f"https://cdn.discordapp.com/emojis/{emoji_id}.png"
    except Exception:
        return None


# ---------------- Text expansion ----------------

def _format_now_vienna() -> str:
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Vienna")
        return datetime.now(timezone.utc).astimezone(tz).strftime("%a, %d %b %Y %H:%M")
    except Exception:
        return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M UTC")

def _expand_basic(
    text: str,
    guild: discord.Guild,
    tag: str,
    clan_name: str,
    inviter: Optional[discord.Member],
    target: Optional[discord.Member],
) -> str:
    if not text:
        return ""
    parts = {
        "{MENTION}": (target.mention if target else ""),
        "{USERNAME}": (target.display_name if target else ""),
        "{CLAN}": clan_name or tag,
        "{CLANTAG}": tag,
        "{GUILD}": guild.name,
        "{NOW}": _format_now_vienna(),
        "{INVITER}": (inviter.display_name if inviter else ""),
    }
    for k, v in parts.items():
        text = text.replace(k, v)
    return _replace_emoji_tokens(text, guild)

def _strip_empty_role_lines(text: str) -> str:
    """
    1) Remove 'Clan Lead:' / 'Deputies:' lines when the value is empty/placeholder.
       Works even if the label and colon are wrapped in markdown (**bold**, _italics_) or
       preceded by emoji bullets. Accepts ASCII ':' and full-width '：'.
    2) If both role lines are removed, also remove the nearby 'Your … crew:' header.
    """
    def norm(s: str) -> str:
        # normalize NBSPs and trim
        return (s or "").replace("\u00A0", " ").strip()

    def strip_md(s: str) -> str:
        # remove light markdown that can sit around labels/colons
        return re.sub(r"[`*_~]", "", s)

    def emptish(val: str) -> bool:
        v = norm(val).lower()
        return v in {"", "-", "—", "n/a", "na", "none", "notfound", "not found"}

    lines = (text or "").splitlines()

    # --- pass 1: drop empty role lines (detect on a "plain" copy with md stripped)
    kept = []
    role_kept_any = False
    role_re = re.compile(r"(Clan\s*Lead|Deput(?:y|ies))\s*[:：]\s*(.*)$", re.IGNORECASE)

    for ln in lines:
        raw = norm(ln)
        plain = strip_md(raw)
        m = role_re.search(plain)
        if m:
            tail = m.group(2)
            if emptish(tail):
                # whole line is just the label + empty value -> drop it
                continue
            role_kept_any = True
        kept.append(ln)

    # --- pass 2: if no role lines survived, remove the orphan "Your … crew:" header
    if not role_kept_any:
        out = []
        i = 0
        while i < len(kept):
            raw = norm(kept[i])
            plain = strip_md(raw)
            if re.search(r"\byour\b.*\bcrew\s*[:：]\s*$", plain, re.IGNORECASE):
                # skip header
                i += 1
                # and a single blank line after it, if present
                if i < len(kept) and not norm(kept[i]):
                    i += 1
                continue
            out.append(kept[i])
            i += 1
        kept = out

    # collapse extra blank lines
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip("\n")
    return cleaned


# ---------------- Row fallback merge ----------------

def _merge_text_fields(primary: dict, default_row: Optional[dict]) -> dict:
    """
    Copy of `primary` where empty TITLE/BODY/FOOTER are filled from default_row (C1C).
    Non-text fields remain from primary (TARGET_CHANNEL_ID, CREST_URL, CLAN, PING_USER, etc.).
    """
    out = dict(primary)
    if not default_row:
        return out
    for k in ("TITLE", "BODY", "FOOTER"):
        if not (out.get(k) or "").strip():
            out[k] = default_row.get(k, "") or ""
    return out


# ---------------- Cog ----------------

class Welcome(commands.Cog):
    """Welcome module for Matchmaker."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        get_rows: Callable[[], List[Dict[str, Any]]],
        log_channel_id: int,
        general_channel_id: Optional[int],
        allowed_role_ids: set[int],
        c1c_footer_emoji_id: Optional[int] = None,
        enabled_default: bool = True,
    ):
        self.bot = bot
        self.get_rows = get_rows
        self.log_channel_id = log_channel_id
        self.general_channel_id = general_channel_id
        self.allowed_role_ids = {int(r) for r in allowed_role_ids if str(r).isdigit()}
        self.c1c_footer_emoji_id = int(c1c_footer_emoji_id) if c1c_footer_emoji_id else None
        self.enabled_default = bool(enabled_default)
        self.enabled_override: Optional[bool] = None

        self.cache: Dict[str, Dict[str, Any]] = {}   # TAG -> row
        self.default_row: Optional[Dict[str, Any]] = None

    # ----- state -----

    @property
    def enabled(self) -> bool:
        return self.enabled_override if self.enabled_override is not None else self.enabled_default

    async def reload_templates(self, _ctx_user: Optional[discord.Member] = None):
        try:
            rows = self.get_rows()
            cache, default_row = {}, None
            for r in rows:
                tag = str(r.get("TAG", "")).strip()
                if not tag:
                    continue
                key = tag.upper()
                row = {
                    "TAG": key,
                    "TARGET_CHANNEL_ID": str(r.get("TARGET_CHANNEL_ID", "")).strip(),
                    "TITLE": r.get("TITLE", "") or "",
                    "BODY": r.get("BODY", "") or "",
                    "FOOTER": r.get("FOOTER", "") or "",
                    "CREST_URL": str(r.get("CREST_URL", "")).strip(),
                    "PING_USER": str(r.get("PING_USER", "")).strip().upper() == "Y",
                    "ACTIVE": str(r.get("ACTIVE", "")).strip().upper() == "Y",
                    "CLAN": r.get("CLAN", "") or "",
                    "CLANLEAD": r.get("CLANLEAD", "") or "",
                    "DEPUTIES": r.get("DEPUTIES", "") or "",
                    "GENERAL_NOTICE": r.get("GENERAL_NOTICE", "") or "",
                }
                if key == "C1C":
                    default_row = row
                else:
                    cache[key] = row
            self.cache, self.default_row = cache, default_row
        except Exception as e:
            await log_to_channel(self.bot, self.log_channel_id, "ERROR",
                "Sheet error while loading templates", error=repr(e))
            raise
        await log_to_channel(self.bot, self.log_channel_id, "INFO",
            "Templates reloaded", rows=len(self.cache), has_default=bool(self.default_row))

    def _effective_row(self, tag: str) -> Optional[Dict[str, Any]]:
        """
        Use clan row for routing; fill missing TITLE/BODY/FOOTER from C1C row.
        If clan row doesn't exist at all -> None (we can't route without its channel).
        """
        key = tag.upper()
        clan = self.cache.get(key)
        if not clan:
            return None
        return _merge_text_fields(clan, self.default_row)

    def _expand_all(
        self,
        text: str,
        guild: discord.Guild,
        tag: str,
        clan_name: str,
        inviter,
        target,
        clanlead: str,
        deputies: str
    ) -> str:
        text = (text or "")
        text = text.replace("{CLANLEAD}", clanlead or "")
        text = text.replace("{DEPUTIES}", deputies or "")
        text = _expand_basic(text, guild, tag, clan_name, inviter, target)
        return _strip_empty_role_lines(text)

    def _has_permission(self, member: discord.Member) -> bool:
        # If no allowed roles configured, allow everyone (for testing).
        if not self.allowed_role_ids:
            return True
        member_roles = {int(r.id) for r in getattr(member, "roles", [])}
        return bool(member_roles & self.allowed_role_ids)

    async def _send_general_notice(self, guild: discord.Guild, text: str,
                                   mention_target: Optional[discord.Member], tag: str, clan_name: str):
        if not self.general_channel_id:
            await log_to_channel(self.bot, self.log_channel_id, "INFO",
                "General notice skipped", cause="general channel not set")
            return
        try:
            ch = guild.get_channel(self.general_channel_id) or await self.bot.fetch_channel(self.general_channel_id)
        except Exception as e:
            await log_to_channel(self.bot, self.log_channel_id, "WARN",
                "General notice skipped", cause="cannot access general channel", error=repr(e))
            return
        expanded = _expand_basic(text, guild, tag, clan_name, inviter=None, target=mention_target)
        try:
            await ch.send(expanded)
        except Exception as e:
            await log_to_channel(self.bot, self.log_channel_id, "WARN",
                "General notice failed", error=repr(e), tag=tag)

    # ----- commands -----

    @commands.command(name="welcome")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def welcome(self, ctx: commands.Context, clantag: str, *args):
        if not self.enabled:
            await log_to_channel(self.bot, self.log_channel_id, "INFO",
                "Module disabled • ignored command", user=f"@{ctx.author.display_name}", tag=clantag.upper())
            return await ctx.reply("The welcome module is currently **off**.")

        if not self._has_permission(ctx.author):
            await log_to_channel(self.bot, self.log_channel_id, "ERROR",
                "Permission denied", user=f"@{ctx.author.display_name}", tag=clantag.upper())
            return await ctx.reply("You're not allowed to use `!welcome`.")

        # resolve target member: explicit mention or reply target
        target_member = ctx.message.mentions[0] if ctx.message.mentions else None
        if (not target_member) and getattr(ctx.message, "reference", None):
            ref = ctx.message.reference
            if ref and ref.resolved and hasattr(ref.resolved, "author"):
                try:
                    target_member = await ctx.guild.fetch_member(ref.resolved.author.id)
                except Exception:
                    target_member = None

        tag = clantag.upper()
        eff = self._effective_row(tag)
        if not eff:
            await log_to_channel(self.bot, self.log_channel_id, "ERROR",
                "Failed to post", tag=tag, cause="no clan row", action="skipped general notice")
            return await ctx.reply(f"I can't find a configured welcome for **{tag}**. Add it in the sheet.")

        chan_id = eff.get("TARGET_CHANNEL_ID", "")
        if not chan_id.isdigit():
            await log_to_channel(self.bot, self.log_channel_id, "ERROR",
                "Failed to post", tag=tag, cause="missing/invalid TARGET_CHANNEL_ID", action="skipped general notice")
            return await ctx.reply(f"No target channel configured for **{tag}**.")

        # Build expanded parts (with per-field C1C fallback already merged)
        clan_name = (eff.get("CLAN", "") or "").strip() or tag

        title = self._expand_all(eff.get("TITLE", ""), ctx.guild, tag, clan_name,
                                 ctx.author, target_member, eff.get("CLANLEAD",""), eff.get("DEPUTIES",""))
        body  = self._expand_all(eff.get("BODY", ""),  ctx.guild, tag, clan_name,
                                 ctx.author, target_member, eff.get("CLANLEAD",""), eff.get("DEPUTIES",""))
        foot  = self._expand_all(eff.get("FOOTER", ""), ctx.guild, tag, clan_name,
                                 ctx.author, target_member, eff.get("CLANLEAD",""), eff.get("DEPUTIES",""))

        # Guard: BODY must exist (Discord requires description)
        if not (body or "").strip():
            await log_to_channel(self.bot, self.log_channel_id, "ERROR",
                "Effective BODY empty even after fallback", tag=tag)
            return await ctx.reply("Missing welcome text. Please check the **C1C** row in the sheet.")

        embed = discord.Embed(title=title or None, description=body, color=discord.Color.blue())
        embed.timestamp = datetime.now(timezone.utc)

        # footer with C1C emoji icon
        if foot:
            icon_url = None
            if self.c1c_footer_emoji_id:
                icon_url = _emoji_cdn_url_from_id(ctx.guild, self.c1c_footer_emoji_id)
            if icon_url:
                embed.set_footer(text=foot, icon_url=icon_url)
            else:
                embed.set_footer(text=foot)

        # crest as thumbnail (optional)
        crest = eff.get("CREST_URL", "")
        if crest:
            try:
                embed.set_thumbnail(url=crest)
            except Exception:
                await log_to_channel(self.bot, self.log_channel_id, "WARN", "Crest load failed", tag=tag)

        # resolve clan channel & send embed
        try:
            channel = ctx.guild.get_channel(int(chan_id)) or await self.bot.fetch_channel(int(chan_id))
        except Exception as e:
            await log_to_channel(self.bot, self.log_channel_id, "ERROR",
                "Failed to post", tag=tag, cause="cannot access clan channel", channel=chan_id, action="skipped general notice")
            return await ctx.reply("Couldn't access the clan channel.")

        content_ping = target_member.mention if (target_member and eff.get("PING_USER")) else ""
        try:
            await channel.send(content=content_ping, embed=embed)
        except Exception as e:
            await log_to_channel(self.bot, self.log_channel_id, "ERROR",
                "Discord send failed", tag=tag, channel=chan_id, error=repr(e), action="skipped general notice")
            return await ctx.reply("Couldn't post the welcome in the clan channel.")

        # general notice (C1C.GENERAL_NOTICE or default copy)
        gen_text = (self.default_row.get("GENERAL_NOTICE", "") if self.default_row else "") or \
                   ("A new flame joins the cult — welcome {MENTION} to {CLAN}!\n"
                    "Be loud, be nerdy, and maybe even helpful. You know the drill, C1C.")
        await self._send_general_notice(ctx.guild, gen_text, target_member, tag, clan_name)

        # cleanup the invoking command to keep chats tidy
        try:
            await asyncio.sleep(2)
            await ctx.message.delete()
        except Exception:
            await log_to_channel(self.bot, self.log_channel_id, "WARN",
                "Cleanup warning • message delete failed",
                channel=getattr(ctx.channel, 'id', None), user=f"@{ctx.author.display_name}")

    @commands.command(name="welcome-refresh")
    async def welcome_refresh(self, ctx: commands.Context):
        if not self._has_permission(ctx.author):
            await log_to_channel(self.bot, self.log_channel_id, "ERROR",
                "Permission denied (refresh)", user=f"@{ctx.author.display_name}")
            return await ctx.reply("Not allowed.")
        try:
            await self.reload_templates(ctx.author)
            await ctx.reply("Welcome templates reloaded. ✅")
        except Exception as e:
            await ctx.reply(f"Reload failed: `{e}`")

    @commands.command(name="welcome-on")
    async def welcome_on(self, ctx: commands.Context):
        if not self._has_permission(ctx.author):
            return await ctx.reply("Not allowed.")
        self.enabled_override = True
        await log_to_channel(self.bot, self.log_channel_id, "INFO",
            "Module enabled by user", user=f"@{ctx.author.display_name}")
        await ctx.reply("Welcome module: **ON**")

    @commands.command(name="welcome-off")
    async def welcome_off(self, ctx: commands.Context):
        if not self._has_permission(ctx.author):
            return await ctx.reply("Not allowed.")
        self.enabled_override = False
        await log_to_channel(self.bot, self.log_channel_id, "INFO",
            "Module disabled by user", user=f"@{ctx.author.display_name}")
        await ctx.reply("Welcome module: **OFF**")

    @commands.command(name="welcome-status")
    async def welcome_status(self, ctx: commands.Context):
        state = "ENABLED" if self.enabled else "DISABLED"
        src = "runtime_override" if self.enabled_override is not None else "env_default"
        await log_to_channel(self.bot, self.log_channel_id, "INFO",
            "Status query", state=state, source=src)
        await ctx.reply(f"Welcome module is **{state}** (source: {src}).")

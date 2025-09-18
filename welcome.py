# modules/welcome.py
# C1C Matchmaker — Welcome Module 
# embed welcome message to clan chat + hype to General
# Logs with [c1c-matchmaker/welcome/<LEVEL>] and never fails silently.

import asyncio, re
from datetime import datetime, timezone
from typing import Callable, Dict, Any, List, Optional

import discord
from discord.ext import commands

# ===== Helpers: logging (to channel + console) =====

def _fmt_kv(**kv) -> str:
    return " ".join(f"{k}={v}" for k, v in kv.items() if v is not None)

async def log_to_channel(bot: commands.Bot, log_channel_id: int, level: str, msg: str, **kv):
    prefix = f"[c1c-matchmaker/welcome/{level}]"
    line = f"{prefix} {msg}"
    if kv:
        line += f" • {_fmt_kv(**kv)}"
# console
    print(line)
# channel
    try:
        ch = bot.get_channel(log_channel_id) or await bot.fetch_channel(log_channel_id)
        if ch:
            await ch.send(line)
    except Exception:
# don't recurse on logging failures
        pass

# ===== Helpers: placeholder + emoji =====

_EMOJI_TOKEN = re.compile(r"{EMOJI:([^}]+)}")

def _sanitize_emoji_name(name: str) -> str:
# Lowercase + strip everything except [a-z0-9_]
    return re.sub(r"[^a-z0-9_]", "", name.lower())

def _resolve_emoji(guild: discord.Guild, token: str) -> str:
    token = token.strip()
# Try by ID
    if token.isdigit():
        for e in guild.emojis:
            if str(e.id) == token:
                return f"<{'a' if e.animated else ''}:{e.name}:{e.id}>".replace("<:", "<:").replace("<a:", "<a:")
        return token
# Try by sanitized name
    s = _sanitize_emoji_name(token)
    for e in guild.emojis:
        if e.name.lower() == s:
            return f"<{'a' if e.animated else ''}:{e.name}:{e.id}>".replace("<:", "<:").replace("<a:", "<a:")
    return token

def _replace_emoji_tokens(text: str, guild: discord.Guild) -> str:
    return _EMOJI_TOKEN.sub(lambda m: _resolve_emoji(guild, m.group(1)), text or "")

def _emoji_cdn_url_from_id(guild: discord.Guild, emoji_id: int) -> Optional[str]:
    """Return the Discord CDN URL for a guild emoji ID, picking .gif if animated."""
    try:
        for e in guild.emojis:
            if e.id == emoji_id:
                ext = "gif" if e.animated else "png"
                return f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
# If not in guild cache, default to png; Discord will still serve it if exists.
        return f"https://cdn.discordapp.com/emojis/{emoji_id}.png"
    except Exception:
        return None


def _format_now_vienna() -> str:
# keep deterministic: Europe/Vienna if available, else UTC
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Vienna")
        return datetime.now(timezone.utc).astimezone(tz).strftime("%a, %d %b %Y %H:%M")
    except Exception:
        return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M UTC")

def _expand(text: str, guild: discord.Guild, tag: str,
            inviter: Optional[discord.Member], target: Optional[discord.Member]) -> str:
    if not text:
        return ""
    parts = {
        "{MENTION}": (target.mention if target else ""),
        "{USERNAME}": (target.display_name if target else ""),
        "{CLAN}": tag,
        "{GUILD}": guild.name,
        "{NOW}": _format_now_vienna(),
        "{INVITER}": (inviter.display_name if inviter else ""),
    }
    for k, v in parts.items():
        text = text.replace(k, v)
    return _replace_emoji_tokens(text, guild)

def _strip_empty_role_lines(text: str) -> str:
# Remove lines where CLANLEAD/DEPUTIES placeholders resolved to blank
    lines = (text or "").splitlines()
    cleaned = []
    for ln in lines:
        raw = ln.strip()
        if ("Clan Lead" in raw or "Deputies" in raw) and re.search(r":\s*$", raw):
# ends with ":" or ": <empty>"
            continue
        cleaned.append(ln)
# Collapse multiple blank lines
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip("\n")
    return out

# ===== Cog =====

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
        c1c_footer_emoji_id: Optional[int] = None,   # <-- CHANGED: emoji ID instead of URL
        enabled_default: bool = True,
    ):
        self.bot = bot
        self.get_rows = get_rows
        self.log_channel_id = log_channel_id
        self.general_channel_id = general_channel_id
        self.allowed_role_ids = {int(r) for r in allowed_role_ids if str(r).isdigit()}
        self.c1c_footer_emoji_id = int(c1c_footer_emoji_id) if c1c_footer_emoji_id else None  # <-- store ID
        self.enabled_default = bool(enabled_default)
        self.enabled_override: Optional[bool] = None
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.default_row: Optional[Dict[str, Any]] = None

    
# ----- internal state -----
    @property
    def enabled(self) -> bool:
        return self.enabled_override if self.enabled_override is not None else self.enabled_default

    async def reload_templates(self, ctx_user: Optional[discord.Member] = None):
        try:
            rows = self.get_rows()  # list of dicts
            cache = {}
            default_row = None
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
            "Templates reloaded",
            rows=len(self.cache), has_default=bool(self.default_row))

    def _effective_row(self, tag: str) -> Optional[Dict[str, Any]]:
        """Return merged row for tag:
           - If clan row ACTIVE=Y, use it.
           - Else if clan row exists but inactive/partial: merge C1C text into it.
           - Else if no clan row: return None (cannot route without channel)."""
        key = tag.upper()
        clan = self.cache.get(key)
        if clan and clan.get("ACTIVE"):
            return clan
# Merge text from C1C into clan scaffold
        if clan and self.default_row:
            merged = dict(clan)
# text from C1C
            for k in ("TITLE", "BODY", "FOOTER", "PING_USER"):
                merged[k] = self.default_row.get(k, "")
            return merged
# No clan row -> cannot resolve channel; return None (we will log & abort)
        return None

    def _expand_all(self, text: str, guild: discord.Guild, tag: str, inviter, target, clanlead: str, deputies: str) -> str:
# Fill simple placeholders first
        text = (text or "")
        text = text.replace("{CLANLEAD}", clanlead or "")
        text = text.replace("{DEPUTIES}", deputies or "")
        text = _expand(text, guild, tag, inviter, target)
        text = _strip_empty_role_lines(text)
        return text

    def _has_permission(self, member: discord.Member) -> bool:
        if not self.allowed_role_ids:
            return True
        member_roles = {int(r.id) for r in member.roles}
        return bool(member_roles & self.allowed_role_ids)

    async def _send_general_notice(self, guild: discord.Guild, text: str, mention_target: Optional[discord.Member], tag: str):
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
        expanded = _expand(text, guild, tag, inviter=None, target=mention_target)
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

# Resolve target member: explicit mention or reply target
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
# Cannot resolve channel; log and abort; no general message
            await log_to_channel(self.bot, self.log_channel_id, "ERROR",
                "Failed to post", tag=tag, cause="no clan row or inactive and no C1C text", action="skipped general notice")
            return await ctx.reply(f"I can't find an active welcome for **{tag}**. Ask an admin to add/activate it in the sheet.")

        chan_id = eff.get("TARGET_CHANNEL_ID", "")
        if not chan_id.isdigit():
            await log_to_channel(self.bot, self.log_channel_id, "ERROR",
                "Failed to post", tag=tag, cause="missing/invalid TARGET_CHANNEL_ID", action="skipped general notice")
            return await ctx.reply(f"No target channel configured for **{tag}**.")

# Build embed
        title = self._expand_all(eff.get("TITLE", ""), ctx.guild, tag, ctx.author, target_member, eff.get("CLANLEAD",""), eff.get("DEPUTIES",""))
        body  = self._expand_all(eff.get("BODY", ""),  ctx.guild, tag, ctx.author, target_member, eff.get("CLANLEAD",""), eff.get("DEPUTIES",""))
        foot  = self._expand_all(eff.get("FOOTER", ""), ctx.guild, tag, ctx.author, target_member, eff.get("CLANLEAD",""), eff.get("DEPUTIES",""))
        
        embed = discord.Embed(title=title, description=body, color=discord.Color.blue())
        embed.timestamp = datetime.now(timezone.utc)
        
        if foot:
            icon_url = None
            if self.c1c_footer_emoji_id:
                icon_url = _emoji_cdn_url_from_id(ctx.guild, self.c1c_footer_emoji_id)
            if icon_url:
                embed.set_footer(text=foot, icon_url=icon_url)
            else:
                embed.set_footer(text=foot)

# Resolve clan channel
        channel = ctx.guild.get_channel(int(chan_id)) or await self.bot.fetch_channel(int(chan_id))
# Ping content for clan embed only if template says so
        content_ping = target_member.mention if (target_member and eff.get("PING_USER")) else ""

# Send clan embed
        try:
            await channel.send(content=content_ping, embed=embed)
        except Exception as e:
            await log_to_channel(self.bot, self.log_channel_id, "ERROR",
                "Discord send failed", tag=tag, channel=chan_id, error=repr(e), action="skipped general notice")
            return await ctx.reply("Couldn't post the welcome in the clan channel.")

# Send general notice (always pings; text from C1C.GENERAL_NOTICE or default)
        gen_text = (self.default_row.get("GENERAL_NOTICE", "") if self.default_row else "") or \
                   ("A new flame joins the cult — welcome {MENTION} to {CLAN}!\n"
                    "Be loud, be nerdy, and maybe even helpful. You know the drill, C1C.")
        await self._send_general_notice(ctx.guild, gen_text, target_member, tag)

# Cleanup: delete invoking command
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

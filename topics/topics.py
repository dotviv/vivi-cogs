"""Hand out conversation starters, and let anyone anonymously ask to change the subject."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import List, Optional, Union

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify
from redbot.core.utils.predicates import MessagePredicate

from . import prompts
from ._common.modlog_proxy import ModLogProxy

log = logging.getLogger("red.vivi-cogs.topics")

TOPIC_COLOUR = discord.Colour.blurple()
NOTICE_COLOUR = discord.Colour.gold()

MAX_TOPIC_LENGTH = 300
MAX_COOLDOWN = 3600
RECENT_MEMORY = 10

# The note travels through the bot, so a stray @everyone would otherwise be
# rung by someone the room can't even see.
NO_MENTIONS = discord.AllowedMentions.none()


class Topics(commands.Cog):
    """Hand out conversation starters, and let anyone ask to change the subject."""

    __author__ = "vivirancy"
    __version__ = "1.0.0"

    # A topic change request is a moderative activity, so it belongs in the
    # requester's history like anything else. Anonymity is a property of the
    # channel, not of the record.
    ACTION_TYPES = (
        {"type": "topic_change", "name": "Topic Change Request",
         "color": NOTICE_COLOUR, "emoji": "💬"},
    )

    DEFAULT_GUILD = {
        "changetopic_enabled": True,
        "use_defaults": True,
        "topics": [],
        "recent_topics": [],
        "cooldown": 300,
        "dm_on_success": True,
    }

    DEFAULT_MEMBER = {
        "last_request": None,
    }

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        # This identifier keys every guild's stored settings. Never change it.
        self.config = Config.get_conf(self, identifier=2604118893, force_registration=True)
        self.config.register_guild(**self.DEFAULT_GUILD)
        self.config.register_member(**self.DEFAULT_MEMBER)
        # core_fallback is off deliberately. Red's core [p]case and [p]casesfor
        # carry only @commands.guild_only() with no permission check, so a core
        # case would let any member look up who asked for a topic change. The
        # ModLog cog gates those lookups behind mod permissions; core does not,
        # so when ModLog is absent the request goes unrecorded rather than public.
        self.modlog = ModLogProxy(self, action_types=self.ACTION_TYPES, core_fallback=False)

    async def cog_load(self) -> None:
        await self.modlog.refresh()

    @commands.Cog.listener()
    async def on_cog_add(self, cog: commands.Cog) -> None:
        await self.modlog.on_cog_add(cog)

    def format_help_for_context(self, ctx: commands.Context) -> str:
        return f"{super().format_help_for_context(ctx)}\n\nAuthor: {self.__author__}\nVersion: {self.__version__}"

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        """Drop the cooldown record belonging to ``user_id``.

        Requests already written to a guild's mod-log channel are that guild's
        message history, not ours, so they are out of reach from here.
        """
        for guild_id, members in (await self.config.all_members()).items():
            if user_id in members:
                await self.config.member_from_ids(guild_id, user_id).clear()

    # ------------------------------------------------------------------
    # Topic pool
    # ------------------------------------------------------------------

    @staticmethod
    def _pool(conf: dict) -> List[str]:
        """Every topic this guild can draw from, built-ins included or not."""
        pool = list(prompts.TOPICS) if conf["use_defaults"] else []
        pool.extend(conf["topics"])
        return pool

    async def _next_topic(self, guild: discord.Guild, conf: dict) -> Optional[str]:
        """Draw a topic and remember it, so the same one doesn't land twice running."""
        topic = prompts.pick(self._pool(conf), conf["recent_topics"])
        if topic is None:
            return None
        async with self.config.guild(guild).recent_topics() as recent:
            if topic in recent:
                recent.remove(topic)
            recent.append(topic)
            del recent[:-RECENT_MEMORY]
        return topic

    # ------------------------------------------------------------------
    # Mod log
    # ------------------------------------------------------------------

    async def _log_request(
        self,
        member: discord.Member,
        *,
        channel: Union[discord.abc.GuildChannel, discord.Thread],
        note: Optional[str],
        jump_url: str,
    ) -> bool:
        """Record who asked, and where. Returns whether it landed.

        The request is anonymous in the channel it was made in and nowhere else:
        moderators need to know who asked in order to spot someone leaning on
        the feature. The case names the requester as its target, which also puts
        it in their ``[p]cases`` history alongside everything else.

        Where and what they said go in the reason, since that is the field that
        takes free text and stays last in the embed.
        """
        detail = note or "*No message attached.*"
        reason = (
            f"{detail}\n\n"
            f"Requested in {channel.mention} — [jump to conversation]({jump_url})\n"
            f"Anonymous in the channel, attributed here."
        )

        case = await self.modlog.create_case(
            member.guild,
            action_type="topic_change",
            target=member,
            moderator=member.guild.me,
            reason=reason,
        )

        return case is not None

    # ------------------------------------------------------------------
    # Anonymity
    # ------------------------------------------------------------------

    @staticmethod
    async def _hide_invocation(ctx: commands.Context) -> bool:
        """Make sure nothing on screen ties the requester to the request.

        A slash invocation was never public, so deferring privately is all it
        takes. A prefix invocation has to be deleted first, and if that fails
        the caller abandons the request rather than posting it next to a name
        everyone can still read.
        """
        if ctx.interaction is not None:
            await ctx.defer(ephemeral=True)
            return True
        try:
            await ctx.message.delete()
        except discord.NotFound:
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False
        return True

    async def _reply_private(
        self, ctx: commands.Context, content: str, *, dm: bool = True
    ) -> None:
        """Answer where only the requester can see it.

        Slash gets an ephemeral reply. Prefix gets a DM -- the invoking message
        is gone by now, so there is nothing left in the channel to reply to
        without naming them.

        ``dm`` only ever applies to the prefix path. An ephemeral reply costs
        the member nothing and a deferred interaction *has* to be answered, so
        the only thing worth making optional is the unsolicited DM.
        """
        if ctx.interaction is not None:
            await ctx.send(content, ephemeral=True)
            return
        if not dm:
            return
        try:
            await ctx.author.send(content)
        except discord.Forbidden:
            log.debug("Could not DM %s about their topic request; DMs are closed.", ctx.author.id)
        except discord.HTTPException as error:
            log.warning("Failed to DM %s: %s", ctx.author.id, error)

    async def _cooldown_remaining(self, member: discord.Member, conf: dict) -> float:
        """Seconds left before ``member`` may request again, or ``0``."""
        if not conf["cooldown"]:
            return 0.0
        last = await self.config.member(member).last_request()
        if last is None:
            return 0.0
        elapsed = discord.utils.utcnow().timestamp() - last
        return max(0.0, conf["cooldown"] - elapsed)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.guild_only()
    @commands.hybrid_command(name="topic")
    async def topic(self, ctx: commands.Context) -> None:
        """Suggest a random conversation topic."""
        conf = await self.config.guild(ctx.guild).all()
        topic = await self._next_topic(ctx.guild, conf)
        if topic is None:
            await ctx.send(
                "There are no topics to draw from. Add some with "
                f"`{ctx.clean_prefix}topicset add`, or turn the built-in list back "
                f"on with `{ctx.clean_prefix}topicset defaults`."
            )
            return
        embed = discord.Embed(title="Here's a topic", description=topic, colour=TOPIC_COLOUR)
        await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)

    @commands.guild_only()
    @commands.hybrid_command(name="changetopic")
    @app_commands.describe(message="An optional note to pass along. Still anonymous.")
    async def changetopic(
        self, ctx: commands.Context, *, message: Optional[str] = None
    ) -> None:
        """Anonymously ask the channel to change the subject."""
        # Order matters: the invocation is destroyed before anything at all is
        # posted, so a failure part-way through can never leave the request
        # visible alongside the name of whoever made it.
        if not await self._hide_invocation(ctx):
            await self._reply_private(
                ctx,
                "I couldn't delete your command, so your request is still on screen "
                "with your name on it — I haven't sent it. Use `/changetopic` "
                "instead, or ask an admin to give me **Manage Messages** here.",
            )
            return

        conf = await self.config.guild(ctx.guild).all()
        if not conf["changetopic_enabled"]:
            await self._reply_private(ctx, "Topic change requests are turned off in this server.")
            return

        remaining = await self._cooldown_remaining(ctx.author, conf)
        if remaining:
            ready_at = discord.utils.utcnow() + timedelta(seconds=remaining)
            await self._reply_private(
                ctx,
                "You've already asked recently. You can ask again "
                f"{discord.utils.format_dt(ready_at, 'R')}.",
            )
            return

        note = message.strip()[:MAX_TOPIC_LENGTH] if message else None

        embed = discord.Embed(
            title="Topic change requested",
            description="Someone here would like to move the conversation on to something else.",
            colour=NOTICE_COLOUR,
        )
        if note:
            embed.add_field(name="They added", value=note, inline=False)
        embed.set_footer(text="Sent anonymously.")
        try:
            notice = await ctx.channel.send(embed=embed, allowed_mentions=NO_MENTIONS)
        except (discord.Forbidden, discord.HTTPException):
            log.warning("Could not post a topic request in channel %s.", ctx.channel.id)
            await self._reply_private(
                ctx, "I couldn't post in this channel, so your request didn't go through."
            )
            return

        logged = await self._log_request(
            ctx.author, channel=ctx.channel, note=note, jump_url=notice.jump_url
        )
        await self.config.member(ctx.author).last_request.set(discord.utils.utcnow().timestamp())

        confirmation = "Sent anonymously — nothing in the channel points back to you."
        if not logged:
            confirmation += (
                "\n\nThis server hasn't set up a moderator log for these requests, so "
                "no one has been notified beyond the channel itself."
            )
        # The request went through and the member can see that for themselves in
        # the channel, so this one is a courtesy. Everything else this command
        # says privately is a failure they'd otherwise never learn about, and
        # stays unconditional.
        await self._reply_private(ctx, confirmation, dm=conf["dm_on_success"])

    # ------------------------------------------------------------------
    # Configuration commands
    # ------------------------------------------------------------------

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.group(name="topicset")
    async def topicset(self, ctx: commands.Context) -> None:
        """Configure conversation topics and change requests."""

    @topicset.command(name="add")
    async def topicset_add(self, ctx: commands.Context, *, topic: str) -> None:
        """Add a custom topic to this server's list."""
        topic = topic.strip()
        if not topic:
            await ctx.send("That's an empty topic.")
            return
        if len(topic) > MAX_TOPIC_LENGTH:
            await ctx.send(f"Keep topics to {MAX_TOPIC_LENGTH} characters or fewer.")
            return
        async with self.config.guild(ctx.guild).topics() as topics:
            if topic in topics:
                await ctx.send("That topic is already on the list.")
                return
            topics.append(topic)
            position = len(topics)
        await ctx.send(f"Added as custom topic **{position}**.")

    @topicset.command(name="remove")
    async def topicset_remove(self, ctx: commands.Context, index: int) -> None:
        """Remove a custom topic by its number from `[p]topicset list`."""
        async with self.config.guild(ctx.guild).topics() as topics:
            if not 1 <= index <= len(topics):
                await ctx.send(
                    f"Pick a number between 1 and {len(topics)}."
                    if topics
                    else "There are no custom topics to remove."
                )
                return
            removed = topics.pop(index - 1)
        await ctx.send(f"Removed: {removed}")

    @topicset.command(name="list")
    async def topicset_list(self, ctx: commands.Context) -> None:
        """List this server's custom topics."""
        conf = await self.config.guild(ctx.guild).all()
        built_in = (
            f"Plus the {len(prompts.TOPICS)} built-in topics."
            if conf["use_defaults"]
            else f"The {len(prompts.TOPICS)} built-in topics are turned off."
        )
        if not conf["topics"]:
            await ctx.send(
                f"No custom topics yet — add one with `{ctx.clean_prefix}topicset add`.\n"
                f"{built_in}"
            )
            return
        listed = "\n".join(f"{i}. {topic}" for i, topic in enumerate(conf["topics"], start=1))
        await ctx.send_interactive(pagify(f"{listed}\n\n{built_in}"))

    @topicset.command(name="clear")
    async def topicset_clear(self, ctx: commands.Context) -> None:
        """Remove every custom topic."""
        topics = await self.config.guild(ctx.guild).topics()
        if not topics:
            await ctx.send("There are no custom topics to clear.")
            return
        await ctx.send(f"Remove all {len(topics)} custom topics? Reply `yes` to confirm.")
        pred = MessagePredicate.yes_or_no(ctx)
        try:
            await self.bot.wait_for("message", check=pred, timeout=30)
        except asyncio.TimeoutError:
            await ctx.send("Timed out — nothing was removed.")
            return
        if not pred.result:
            await ctx.send("Cancelled.")
            return
        await self.config.guild(ctx.guild).topics.set([])
        await self.config.guild(ctx.guild).recent_topics.set([])
        await ctx.send("Custom topics cleared.")

    @topicset.command(name="defaults")
    async def topicset_defaults(self, ctx: commands.Context) -> None:
        """Turn the built-in topic list on or off."""
        conf = await self.config.guild(ctx.guild).all()
        if not conf["use_defaults"]:
            await self.config.guild(ctx.guild).use_defaults.set(True)
            await ctx.send(f"The {len(prompts.TOPICS)} built-in topics are now in the mix.")
            return
        if not conf["topics"]:
            await ctx.send(
                "That would leave nothing to draw from. Add at least one topic with "
                f"`{ctx.clean_prefix}topicset add` first."
            )
            return
        await self.config.guild(ctx.guild).use_defaults.set(False)
        await ctx.send("Built-in topics turned off. Only your custom list will be used.")

    @topicset.command(name="cooldown")
    async def topicset_cooldown(self, ctx: commands.Context, seconds: int) -> None:
        """Set the per-member wait between requests, in seconds (0-3600, 0 disables)."""
        if not 0 <= seconds <= MAX_COOLDOWN:
            await ctx.send(f"Pick a value between 0 and {MAX_COOLDOWN} seconds.")
            return
        await self.config.guild(ctx.guild).cooldown.set(seconds)
        if seconds:
            await ctx.send(f"Members can now request a topic change every **{seconds}** seconds.")
        else:
            await ctx.send(
                "Cooldown disabled. Nothing now limits how often a member can request "
                "a change, and the requests are anonymous to the channel."
            )

    @topicset.command(name="dm")
    async def topicset_dm(self, ctx: commands.Context) -> None:
        """Turn the DM confirming a successful request on or off."""
        enabled = await self.config.guild(ctx.guild).dm_on_success()
        await self.config.guild(ctx.guild).dm_on_success.set(not enabled)
        if enabled:
            await ctx.send(
                "I'll stop DMing members to confirm a request went through. They'll "
                "still be told when one **doesn't** — a silent failure would leave "
                "someone believing they'd been heard when they hadn't.\n\n"
                "This only affects the prefix command. `/changetopic` confirms "
                "privately in Discord itself, which sends no DM either way."
            )
        else:
            await ctx.send(
                f"I'll DM members to confirm `{ctx.clean_prefix}changetopic` went through."
            )

    @topicset.command(name="toggle")
    async def topicset_toggle(self, ctx: commands.Context) -> None:
        """Turn topic change requests on or off."""
        enabled = await self.config.guild(ctx.guild).changetopic_enabled()
        await self.config.guild(ctx.guild).changetopic_enabled.set(not enabled)
        if enabled:
            await ctx.send(f"`{ctx.clean_prefix}changetopic` disabled.")
        else:
            await ctx.send(f"`{ctx.clean_prefix}changetopic` enabled.")

    @topicset.command(name="settings")
    async def topicset_settings(self, ctx: commands.Context) -> None:
        """Show the current configuration."""
        conf = await self.config.guild(ctx.guild).all()

        embed = discord.Embed(title="Topics settings", colour=await ctx.embed_colour())
        embed.add_field(
            name="Change requests",
            value="Enabled" if conf["changetopic_enabled"] else "Disabled",
            inline=True,
        )
        embed.add_field(
            name="Cooldown",
            value=f"{conf['cooldown']}s" if conf["cooldown"] else "*none*",
            inline=True,
        )
        embed.add_field(
            name="Mod log",
            value="ModLog cases" if self.modlog.available else "*ModLog not loaded*",
            inline=True,
        )
        embed.add_field(
            name="Built-in topics",
            value=f"{len(prompts.TOPICS)} in use" if conf["use_defaults"] else "*off*",
            inline=True,
        )
        embed.add_field(name="Custom topics", value=str(len(conf["topics"])), inline=True)
        embed.add_field(name="Total pool", value=str(len(self._pool(conf))), inline=True)
        embed.add_field(
            name="Confirmation DM",
            value="On" if conf["dm_on_success"] else "Off",
            inline=True,
        )
        if not self.modlog.available:
            embed.set_footer(
                text=(
                    "ModLog isn't loaded, so requests aren't being recorded and "
                    "moderators can't see who asks. Red's core modlog is not used "
                    "here: its case lookups are readable by any member, which would "
                    "undo the anonymity. Load it with [p]load modlog."
                )
            )
        await ctx.send(embed=embed)

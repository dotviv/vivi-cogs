import asyncio
from datetime import timedelta
from typing import Optional

import discord
from redbot.core import commands
from redbot.core.bot import Red

from ._common.modlog_proxy import CaseRef, ModLogProxy


def audit_reason(case: CaseRef | None, reason: str | None) -> str:
    """The reason recorded in Discord's own audit log.

    The case number is only available when something actually recorded the
    case, so it is included opportunistically rather than assumed.
    """
    detail = reason or "No reason provided."

    if case is None or case.case_number is None:
        return detail

    return f"Modlog case {case.case_number}: {detail}"


class Moderation(commands.Cog):

    __author__ = "vivirancy"
    __version__ = "1.0.0"

    # Declared as plain data rather than ModLog.ActionType instances. ModLog owns
    # that class, and importing it here would reintroduce the cross-cog import
    # this restructure exists to remove. The proxy pushes these to ModLog when it
    # is loaded, and mirrors them into Red's core modlog for the fallback path.
    ACTION_TYPES = (
        {"type": "warn", "name": "Warning", "color": discord.Colour.yellow(), "emoji": "⚠️"},
        {"type": "kick", "name": "Kick", "color": discord.Colour.yellow(), "emoji": "🦶"},
        {"type": "tempban", "name": "Temporary Ban", "color": discord.Colour.red(), "emoji": "🔨"},
        {"type": "ban", "name": "Ban", "color": discord.Colour.red(), "emoji": "🔨"},
        {"type": "unban", "name": "Unban", "color": discord.Colour.green(), "emoji": "🔨"},
    )

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._background_tasks: set[asyncio.Task] = set()
        self.modlog = ModLogProxy(self, action_types=self.ACTION_TYPES)

    async def cog_load(self) -> None:
        await self.modlog.refresh()

    @commands.Cog.listener()
    async def on_cog_add(self, cog: commands.Cog) -> None:
        await self.modlog.on_cog_add(cog)

    ### ----------------------------------------------------------------
    ### Commands
    ### ----------------------------------------------------------------

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.hybrid_command("warn", aliases=["w"])
    async def warn(self, ctx: commands.Context, target: discord.Member, *, reason: str | None) -> None:
        """Warns a member."""

        guild = ctx.guild

        if not guild:
            return

        confirmation = await self.modlog.confirm_action(
            ctx=ctx,
            target=target,
            action_type="warn",
            reason=reason
        )

        if not confirmation:
            return

        case = await self.modlog.create_case(
            guild,
            action_type="warn",
            target=target,
            moderator=ctx.author,
            reason=reason)

        await self.modlog.send_case_action_summary(ctx, case, ephemeral=False) # Non-ephemeral, warnings need to surface so members can see their warnings.

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.hybrid_command("kick", aliases=["k"])
    async def kick(self, ctx: commands.Context, target: discord.Member, *, reason: str | None) -> None:
        """Kicks a member."""

        guild = ctx.guild

        if not guild:
            return

        confirmation = await self.modlog.confirm_action(
            ctx=ctx,
            target=target,
            action_type="kick",
            reason=reason
        )

        if not confirmation:
            return

        case = await self.modlog.create_case(
            guild,
            action_type="kick",
            target=target,
            moderator=ctx.author,
            reason=reason)

        await guild.kick(target, reason=audit_reason(case, reason))

        await self.modlog.send_case_action_summary(ctx, case)

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.hybrid_command("ban", aliases=["b"])
    async def ban(self, ctx: commands.Context, target: discord.Member, delete_messages: Optional[commands.get_timedelta_converter(maximum=timedelta(days=7))] = None, *, reason: str | None) -> None:  # type: ignore[valid-type]
        """Bans a member."""

        guild = ctx.guild

        if not guild:
            return

        confirmation = await self.modlog.confirm_action(
            ctx=ctx,
            target=target,
            action_type="ban",
            reason=reason
        )

        if not confirmation:
            return

        case = await self.modlog.create_case(
            guild,
            action_type="ban",
            target=target,
            moderator=ctx.author,
            reason=reason)

        await guild.ban(
            target,
            reason=audit_reason(case, reason),
            delete_message_seconds=delete_messages.total_seconds() if delete_messages else 0)

        await self.modlog.send_case_action_summary(ctx, case)

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.hybrid_command("unban", aliases=["ub"])
    async def unban(self, ctx: commands.Context, target: discord.User, *, reason: str | None) -> None:
        """Unbans a member."""

        guild = ctx.guild

        if not guild:
            return

        confirmation = await self.modlog.confirm_action(
            ctx=ctx,
            target=target,
            action_type="unban",
            reason=reason
        )

        if not confirmation:
            return

        try:
            await guild.fetch_ban(target)
        except discord.NotFound:
            await ctx.send(f"{target.mention} is not banned.", ephemeral=True)
            return

        case = await self.modlog.create_case(
            guild,
            action_type="unban",
            target=target,
            moderator=ctx.author,
            reason=reason)

        await guild.unban(target, reason=audit_reason(case, reason))

        await self.modlog.send_case_action_summary(ctx, case)

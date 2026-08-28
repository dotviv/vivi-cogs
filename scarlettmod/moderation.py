import asyncio
from datetime import timedelta
from typing import Optional

import discord
from redbot.core import commands
from redbot.core.bot import Red

from modlog.modlog import ModLog

class Moderation(commands.Cog):

    __author__ = "vivirancy"
    __version__ = "1.0.0"

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._background_tasks: set[asyncio.Task] = set()

        ModLog.register_action_type(ModLog.ActionType(
            type="warn",
            name="Warning",
            color=discord.Colour.yellow(),
            emoji="⚠️"))

        ModLog.register_action_type(ModLog.ActionType(
            type="kick",
            name="Kick",
            color=discord.Colour.yellow(),
            emoji="🦶"))

        ModLog.register_action_type(ModLog.ActionType(
            type="tempban",
            name="Temporary Ban",
            color=discord.Colour.red(),
            emoji="🔨"))

        ModLog.register_action_type(ModLog.ActionType(
            type="ban",
            name="Ban",
            color=discord.Colour.red(),
            emoji="🔨"))

        ModLog.register_action_type(ModLog.ActionType(
            type="unban",
            name="Unban",
            color=discord.Colour.green(),
            emoji="🔨"))

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.hybrid_command("warn", aliases=["w"])
    async def warn(self, ctx: commands.Context, target: discord.Member, *, reason: str | None) -> None:
        """Warns a member."""

        guild = ctx.guild

        if not guild:
            return

        confirmation = await ModLog.confirm_action(
            ctx=ctx,
            target=target,
            action_type="warn",
            reason=reason
        )

        if not confirmation:
            return

        case = await ModLog.create_case(
            bot=self.bot,
            guild=guild,
            action_type="warn",
            target=target,
            moderator=ctx.author,
            reason=reason)

        await ModLog.send_case_action_summary(ctx, case, ephemeral=False) # Non-ephemeral, warnings need to surface so members can see their warnings.

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.hybrid_command("kick", aliases=["k"])
    async def kick(self, ctx: commands.Context, target: discord.Member, *, reason: str | None) -> None:
        """Kicks a member."""

        guild = ctx.guild

        if not guild:
            return

        confirmation = await ModLog.confirm_action(
            ctx=ctx,
            target=target,
            action_type="kick",
            reason=reason
        )

        if not confirmation:
            return

        case = await ModLog.create_case(
            bot=self.bot,
            guild=guild,
            action_type="kick",
            target=target,
            moderator=ctx.author,
            reason=reason)

        if not case:
            await ctx.send(f"Failed to kick {target.mention}.", ephemeral=True)
            return

        await guild.kick(target, reason=f"Modlog case {case.case_number}: {reason or 'No reason provided.'}")

        await ModLog.send_case_action_summary(ctx, case)

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.hybrid_command("ban", aliases=["b"])
    async def ban(self, ctx: commands.Context, target: discord.Member, delete_messages: Optional[commands.get_timedelta_converter(maximum=timedelta(days=7))] = None, *, reason: str | None) -> None:  # type: ignore[valid-type]
        """Bans a member."""

        guild = ctx.guild

        if not guild:
            return

        confirmation = await ModLog.confirm_action(
            ctx=ctx,
            target=target,
            action_type="ban",
            reason=reason
        )

        if not confirmation:
            return

        case = await ModLog.create_case(
            bot=self.bot,
            guild=guild,
            action_type="ban",
            target=target,
            moderator=ctx.author,
            reason=reason)

        if not case:
            await ctx.send(f"Failed to ban {target.mention}.", ephemeral=True)
            return

        await guild.ban(target, reason=f"Modlog case {case.case_number}: {reason or 'No reason provided.'}", delete_message_seconds=delete_messages.total_seconds() if delete_messages else 0)

        await ModLog.send_case_action_summary(ctx, case)

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.hybrid_command("unban", aliases=["ub"])
    async def unban(self, ctx: commands.Context, target: discord.User, *, reason: str | None) -> None:
        """Unbans a member."""

        guild = ctx.guild

        if not guild:
            return

        confirmation = await ModLog.confirm_action(
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

        case = await ModLog.create_case(
            bot=self.bot,
            guild=guild,
            action_type="unban",
            target=target,
            moderator=ctx.author,
            reason=reason)

        if not case:
            await ctx.send(f"Failed to unban {target.mention}.", ephemeral=True)
            return

        await guild.unban(target, reason=f"Modlog case {case.case_number}: {reason or 'No reason provided.'}")

        await ModLog.send_case_action_summary(ctx, case)
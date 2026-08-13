"""Gate new members behind an image captcha in a dedicated channel."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_list

from . import captcha

log = logging.getLogger("red.vivi-cogs.verification")

FAILURE_ACTIONS = ("none", "kick")


class Verification(commands.Cog):
    """Require new members to solve an image captcha before they are let in."""

    __author__ = "vivinancy"
    __version__ = "1.0.0"

    DEFAULT_GUILD = {
        "enabled": False,
        "channel_id": None,
        "join_roles": [],
        "add_roles": [],
        "remove_roles": [],
        "code_length": 6,
        "max_attempts": 3,
        "timeout": 600,
        "on_failure": "none",
        "delete_messages": True,
    }

    DEFAULT_MEMBER = {
        "code": None,
        "attempts": 0,
        "expires_at": None,
        "message_id": None,
    }

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        # This identifier keys every guild's stored settings. Never change it.
        self.config = Config.get_conf(self, identifier=1357924680, force_registration=True)
        self.config.register_guild(**self.DEFAULT_GUILD)
        self.config.register_member(**self.DEFAULT_MEMBER)
        self._sweep_expired.start()

    def cog_unload(self) -> None:
        self._sweep_expired.cancel()

    def format_help_for_context(self, ctx: commands.Context) -> str:
        return f"{super().format_help_for_context(ctx)}\n\nAuthor: {self.__author__}\nVersion: {self.__version__}"

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        """Drop any pending verification record belonging to ``user_id``."""
        for guild_id, members in (await self.config.all_members()).items():
            if user_id in members:
                await self.config.member_from_ids(guild_id, user_id).clear()

    # ------------------------------------------------------------------
    # Role safety
    # ------------------------------------------------------------------

    def _role_problem(self, guild: discord.Guild, role: discord.Role) -> Optional[str]:
        """Return why the bot cannot manage ``role``, or ``None`` if it can.

        Discord fails role assignment *silently* when the hierarchy is wrong, so
        this is checked at configuration time rather than at 3am during a raid.
        """
        me = guild.me
        if role.is_default():
            return "That's the `@everyone` role, which can't be assigned."
        if role.managed:
            return f"{role.mention} is managed by an integration and can't be assigned manually."
        if not me.guild_permissions.manage_roles:
            return "I don't have the **Manage Roles** permission in this server."
        if role >= me.top_role:
            return (
                f"{role.mention} is above (or equal to) my highest role, so I can't manage it. "
                "Move my role higher in **Server Settings → Roles**."
            )
        return None

    async def _apply_roles(
        self,
        member: discord.Member,
        *,
        add: List[int],
        remove: List[int],
        reason: str,
    ) -> None:
        guild = member.guild
        to_add = [r for r in (guild.get_role(i) for i in add) if r and r not in member.roles]
        to_remove = [r for r in (guild.get_role(i) for i in remove) if r and r in member.roles]
        try:
            if to_add:
                await member.add_roles(*to_add, reason=reason)
            if to_remove:
                await member.remove_roles(*to_remove, reason=reason)
        except discord.Forbidden:
            log.warning(
                "Missing permissions to update roles for %s in guild %s.", member.id, guild.id
            )
        except discord.HTTPException as error:
            log.warning("Failed to update roles for %s: %s", member.id, error)

    # ------------------------------------------------------------------
    # Verification flow
    # ------------------------------------------------------------------

    async def _issue_captcha(
        self,
        member: discord.Member,
        channel: discord.TextChannel,
        conf: dict,
        *,
        attempts: Optional[int] = None,
    ) -> None:
        """Generate a fresh code, persist it, and post the image."""
        code = captcha.generate_code(conf["code_length"])
        expires_at = datetime.now(timezone.utc).timestamp() + conf["timeout"]

        await self._delete_previous_prompt(member, channel)

        embed = discord.Embed(
            title="Verification required",
            description=(
                f"Welcome to **{member.guild.name}**!\n\n"
                f"Type the **{conf['code_length']} characters** shown below to gain access.\n"
                f"Letters are not case-sensitive."
            ),
            colour=discord.Colour.blurple(),
        )
        embed.set_image(url="attachment://captcha.png")
        remaining = conf["max_attempts"] if attempts is None else attempts
        embed.set_footer(text=f"{remaining} attempt(s) remaining")

        try:
            message = await channel.send(
                content=member.mention,
                embed=embed,
                file=discord.File(captcha.render(code), filename="captcha.png"),
            )
        except discord.Forbidden:
            log.warning(
                "Cannot post in verification channel %s of guild %s.", channel.id, member.guild.id
            )
            return

        await self.config.member(member).set(
            {
                "code": code,
                "attempts": remaining,
                "expires_at": expires_at,
                "message_id": message.id,
            }
        )

    async def _delete_previous_prompt(
        self, member: discord.Member, channel: discord.abc.Messageable
    ) -> None:
        message_id = await self.config.member(member).message_id()
        if not message_id:
            return
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def _succeed(self, member: discord.Member, conf: dict) -> None:
        await self._apply_roles(
            member,
            add=conf["add_roles"],
            remove=conf["remove_roles"],
            reason="Verification: captcha solved",
        )
        await self.config.member(member).clear()

    async def _fail(self, member: discord.Member, conf: dict, reason: str) -> None:
        await self.config.member(member).clear()
        if conf["on_failure"] != "kick":
            return
        try:
            await member.kick(reason=f"Verification: {reason}")
        except discord.Forbidden:
            log.warning(
                "Configured to kick on failed verification but missing permissions in guild %s.",
                member.guild.id,
            )
        except discord.HTTPException as error:
            log.warning("Failed to kick %s: %s", member.id, error)

    # ------------------------------------------------------------------
    # Listeners
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        conf = await self.config.guild(member.guild).all()
        if not conf["enabled"]:
            return

        if conf["join_roles"]:
            await self._apply_roles(
                member, add=conf["join_roles"], remove=[], reason="Verification: pending"
            )

        channel = member.guild.get_channel(conf["channel_id"]) if conf["channel_id"] else None
        if channel is None:
            log.warning("Verification channel is missing in guild %s.", member.guild.id)
            return
        await self._issue_captcha(member, channel, conf)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        conf = await self.config.guild(message.guild).all()
        if not conf["enabled"] or message.channel.id != conf["channel_id"]:
            return

        member = message.author
        state = await self.config.member(member).all()
        if not state["code"]:
            return

        if conf["delete_messages"]:
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass

        if message.content.strip().upper() == state["code"].upper():
            await self._delete_previous_prompt(member, message.channel)
            await self._succeed(member, conf)
            await message.channel.send(
                f"{member.mention} Verified — welcome to **{message.guild.name}**!",
                delete_after=15,
            )
            return

        remaining = state["attempts"] - 1
        if remaining <= 0:
            await self._delete_previous_prompt(member, message.channel)
            await self._fail(member, conf, "ran out of attempts")
            await message.channel.send(
                f"{member.mention} That was your last attempt. "
                "Please contact a moderator if you need help.",
                delete_after=30,
            )
            return

        # Always burn the failed code and issue a new one.
        await self._issue_captcha(member, message.channel, conf, attempts=remaining)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self.config.member(member).clear()

    # ------------------------------------------------------------------
    # Expiry sweep
    # ------------------------------------------------------------------

    @tasks.loop(seconds=60)
    async def _sweep_expired(self) -> None:
        now = datetime.now(timezone.utc).timestamp()
        for guild_id, members in (await self.config.all_members()).items():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            conf = await self.config.guild(guild).all()
            for member_id, state in members.items():
                if not state.get("expires_at") or state["expires_at"] > now:
                    continue
                member = guild.get_member(member_id)
                if member is None:
                    await self.config.member_from_ids(guild_id, member_id).clear()
                    continue
                await self._fail(member, conf, "did not verify in time")

    @_sweep_expired.before_loop
    async def _before_sweep(self) -> None:
        await self.bot.wait_until_red_ready()

    # ------------------------------------------------------------------
    # Configuration commands
    # ------------------------------------------------------------------

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.group(name="verifyset")
    async def verifyset(self, ctx: commands.Context) -> None:
        """Configure member verification."""

    @verifyset.command(name="channel")
    async def verifyset_channel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        """Set the channel where captchas are posted."""
        perms = channel.permissions_for(ctx.guild.me)
        missing = [
            name
            for name, has in (
                ("Send Messages", perms.send_messages),
                ("Embed Links", perms.embed_links),
                ("Attach Files", perms.attach_files),
                ("Manage Messages", perms.manage_messages),
            )
            if not has
        ]
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        message = f"Verification channel set to {channel.mention}."
        if missing:
            message += f"\n\nHeads up — I'm missing {humanize_list(missing)} there."
        await ctx.send(message)

    async def _modify_role_list(
        self, ctx: commands.Context, key: str, role: discord.Role, *, adding: bool
    ) -> None:
        if adding:
            problem = self._role_problem(ctx.guild, role)
            if problem:
                await ctx.send(problem)
                return
        async with self.config.guild(ctx.guild).get_attr(key)() as roles:
            if adding:
                if role.id in roles:
                    await ctx.send(f"{role.mention} is already in that list.")
                    return
                roles.append(role.id)
            else:
                if role.id not in roles:
                    await ctx.send(f"{role.mention} isn't in that list.")
                    return
                roles.remove(role.id)
        await ctx.send(
            f"{role.mention} {'added to' if adding else 'removed from'} the `{key}` list."
        )

    @verifyset.group(name="joinrole")
    async def verifyset_joinrole(self, ctx: commands.Context) -> None:
        """Roles applied the moment someone joins (e.g. Unverified)."""

    @verifyset_joinrole.command(name="add")
    async def joinrole_add(self, ctx: commands.Context, role: discord.Role) -> None:
        """Apply this role on join."""
        await self._modify_role_list(ctx, "join_roles", role, adding=True)

    @verifyset_joinrole.command(name="remove")
    async def joinrole_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        """Stop applying this role on join."""
        await self._modify_role_list(ctx, "join_roles", role, adding=False)

    @verifyset.group(name="addrole")
    async def verifyset_addrole(self, ctx: commands.Context) -> None:
        """Roles granted once verification succeeds."""

    @verifyset_addrole.command(name="add")
    async def addrole_add(self, ctx: commands.Context, role: discord.Role) -> None:
        """Grant this role on success."""
        await self._modify_role_list(ctx, "add_roles", role, adding=True)

    @verifyset_addrole.command(name="remove")
    async def addrole_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        """Stop granting this role on success."""
        await self._modify_role_list(ctx, "add_roles", role, adding=False)

    @verifyset.group(name="removerole")
    async def verifyset_removerole(self, ctx: commands.Context) -> None:
        """Roles stripped once verification succeeds."""

    @verifyset_removerole.command(name="add")
    async def removerole_add(self, ctx: commands.Context, role: discord.Role) -> None:
        """Strip this role on success."""
        await self._modify_role_list(ctx, "remove_roles", role, adding=True)

    @verifyset_removerole.command(name="remove")
    async def removerole_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        """Stop stripping this role on success."""
        await self._modify_role_list(ctx, "remove_roles", role, adding=False)

    @verifyset.command(name="attempts")
    async def verifyset_attempts(self, ctx: commands.Context, amount: int) -> None:
        """Set how many tries a member gets (1-10)."""
        if not 1 <= amount <= 10:
            await ctx.send("Pick a number between 1 and 10.")
            return
        await self.config.guild(ctx.guild).max_attempts.set(amount)
        await ctx.send(f"Members now get **{amount}** attempt(s).")

    @verifyset.command(name="timeout")
    async def verifyset_timeout(self, ctx: commands.Context, seconds: int) -> None:
        """Set how long a captcha stays valid, in seconds (60-86400)."""
        if not 60 <= seconds <= 86400:
            await ctx.send("Pick a value between 60 and 86400 seconds.")
            return
        await self.config.guild(ctx.guild).timeout.set(seconds)
        await ctx.send(f"Captchas now expire after **{seconds}** seconds.")

    @verifyset.command(name="length")
    async def verifyset_length(self, ctx: commands.Context, characters: int) -> None:
        """Set how many characters the code contains (4-10)."""
        if not 4 <= characters <= 10:
            await ctx.send("Pick a length between 4 and 10.")
            return
        await self.config.guild(ctx.guild).code_length.set(characters)
        await ctx.send(f"Codes are now **{characters}** characters long.")

    @verifyset.command(name="onfail")
    async def verifyset_onfail(self, ctx: commands.Context, action: str) -> None:
        """What to do when someone fails or times out: `none` or `kick`."""
        action = action.lower()
        if action not in FAILURE_ACTIONS:
            await ctx.send(f"Choose one of: {humanize_list(list(FAILURE_ACTIONS))}.")
            return
        if action == "kick" and not ctx.guild.me.guild_permissions.kick_members:
            await ctx.send("I need the **Kick Members** permission for that.")
            return
        await self.config.guild(ctx.guild).on_failure.set(action)
        await ctx.send(
            "Members who fail will now be kicked."
            if action == "kick"
            else "Members who fail will stay unverified and can try again."
        )

    @verifyset.command(name="cleanup")
    async def verifyset_cleanup(self, ctx: commands.Context, enabled: bool) -> None:
        """Toggle deleting captcha prompts and member guesses."""
        await self.config.guild(ctx.guild).delete_messages.set(enabled)
        await ctx.send(f"Message cleanup {'enabled' if enabled else 'disabled'}.")

    @verifyset.command(name="toggle")
    async def verifyset_toggle(self, ctx: commands.Context) -> None:
        """Turn verification on or off."""
        conf = await self.config.guild(ctx.guild).all()
        if conf["enabled"]:
            await self.config.guild(ctx.guild).enabled.set(False)
            await ctx.send("Verification disabled.")
            return

        problems = []
        if not conf["channel_id"] or ctx.guild.get_channel(conf["channel_id"]) is None:
            problems.append("Set a verification channel with `[p]verifyset channel`.")
        if not conf["add_roles"] and not conf["remove_roles"]:
            problems.append(
                "Configure at least one role to add or remove with "
                "`[p]verifyset addrole add` or `[p]verifyset removerole add`."
            )
        if problems:
            prefix = ctx.clean_prefix
            listed = "\n".join(f"- {p.replace('[p]', prefix)}" for p in problems)
            await ctx.send(f"Not ready yet:\n{listed}")
            return

        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("Verification enabled. New members will be asked to solve a captcha.")

    @verifyset.command(name="settings")
    async def verifyset_settings(self, ctx: commands.Context) -> None:
        """Show the current configuration."""
        conf = await self.config.guild(ctx.guild).all()

        def render_roles(ids: List[int]) -> str:
            roles = [ctx.guild.get_role(i) for i in ids]
            mentions = [r.mention for r in roles if r]
            return humanize_list(mentions) if mentions else "*none*"

        channel = ctx.guild.get_channel(conf["channel_id"]) if conf["channel_id"] else None

        embed = discord.Embed(
            title="Verification settings",
            colour=await ctx.embed_colour(),
        )
        embed.add_field(
            name="Status", value="Enabled" if conf["enabled"] else "Disabled", inline=True
        )
        embed.add_field(
            name="Channel", value=channel.mention if channel else "*not set*", inline=True
        )
        embed.add_field(name="On failure", value=f"`{conf['on_failure']}`", inline=True)
        embed.add_field(name="Roles on join", value=render_roles(conf["join_roles"]), inline=False)
        embed.add_field(name="Added on success", value=render_roles(conf["add_roles"]), inline=False)
        embed.add_field(
            name="Removed on success", value=render_roles(conf["remove_roles"]), inline=False
        )
        embed.add_field(name="Code length", value=str(conf["code_length"]), inline=True)
        embed.add_field(name="Attempts", value=str(conf["max_attempts"]), inline=True)
        embed.add_field(name="Timeout", value=f"{conf['timeout']}s", inline=True)
        embed.add_field(
            name="Cleanup", value="On" if conf["delete_messages"] else "Off", inline=True
        )
        await ctx.send(embed=embed)

    @verifyset.command(name="test")
    async def verifyset_test(self, ctx: commands.Context) -> None:
        """Preview a captcha here. Changes no roles and records no state."""
        conf = await self.config.guild(ctx.guild).all()
        code = captcha.generate_code(conf["code_length"])
        embed = discord.Embed(
            title="Captcha preview",
            description=f"This is only a preview. The code is ||`{code}`||.",
            colour=await ctx.embed_colour(),
        )
        embed.set_image(url="attachment://captcha.png")
        await ctx.send(
            embed=embed, file=discord.File(captcha.render(code), filename="captcha.png")
        )

    # ------------------------------------------------------------------
    # Manual moderator override
    # ------------------------------------------------------------------

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.group(name="verify")
    async def verify(self, ctx: commands.Context) -> None:
        """Manually resolve a pending verification."""

    @verify.command(name="approve")
    async def verify_approve(self, ctx: commands.Context, member: discord.Member) -> None:
        """Pass a member through without solving the captcha."""
        conf = await self.config.guild(ctx.guild).all()
        await self._succeed(member, conf)
        await ctx.send(f"{member.mention} has been manually verified.")

    @verify.command(name="reject")
    async def verify_reject(self, ctx: commands.Context, member: discord.Member) -> None:
        """Clear a member's pending verification and apply the failure action."""
        conf = await self.config.guild(ctx.guild).all()
        await self._fail(member, conf, f"rejected by {ctx.author}")
        await ctx.send(f"{member.mention}'s verification was rejected.")

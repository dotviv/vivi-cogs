# ---------------------------------------------------------------------------
# GENERATED FILE -- DO NOT EDIT.
#
# Vendored from common/roles.py by tools/sync_common.py.
# Edit the source file and re-run the script; CI rejects drift.
# ---------------------------------------------------------------------------

from typing import Optional, List

import discord

class Roles:

    @staticmethod
    def assignable_role_problem(guild: discord.Guild, *, role: discord.Role) -> Optional[str]:
        """
        Returns an error string if the role specified can either not be assigned or if the bot
        cannot manage the role.
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

    @staticmethod
    def manageable_roles(guild: discord.Guild, roles: List[discord.Role]) -> List[discord.Role]:
        """Roles the bot can actually add/remove: not @everyone, not managed, below our top role."""
        me = guild.me
        return [
            role
            for role in roles
            if not role.is_default() and not role.managed and role < me.top_role
        ]
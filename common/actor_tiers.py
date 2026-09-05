"""Dynamic actor display: what badge an actor's case gets when nobody configured one.

An action type may set an explicit ``actor_label``/``actor_emoji`` (Topics wants
every request filed under "Requester"/🙋, say) -- that always wins, and costs nothing
extra. Absent that, the badge is resolved from the actor's real standing in the
guild: an automated action reads as the bot, a human's badge follows the same
owner/admin/mod/member ladder Red itself uses for permission checks.

This lives here, not in ``modlog_render``, because the ladder needs ``bot`` and is
inherently ``async`` (``is_admin_or_superior``/``is_mod_or_superior`` hit Red's admin
and mod role config), while ``build_case_embed`` is deliberately synchronous and
takes only primitives. Callers resolve a label/emoji pair here first, then pass the
result into ``build_case_embed`` like any other string.
"""

from __future__ import annotations

import discord

from redbot.core.utils.mod import is_admin_or_superior, is_mod_or_superior

BOT = ("Bot", "🤖")
OWNER = ("Owner", "👑")
ADMIN = ("Admin", "⚔️")
MODERATOR = ("Moderator", "🛡️")
MEMBER = ("Member", "👤")
USER = ("User", "👤")


async def resolve_actor_display(
    bot,
    actor: discord.Member | discord.User | int | None,
    *,
    configured_label: str | None,
    configured_emoji: str | None,
) -> tuple[str, str]:
    """The (label, emoji) pair an actor's case field should use.

    A configured label short-circuits everything else -- no Red API calls happen
    at all in that case, so a cog that always wants "Requester"/🙋 never pays for
    a permission lookup it doesn't need.

    Only a live guild member carries roles and permissions to check against; a
    ``discord.User`` (someone who has since left the guild) or a bare ID that
    never resolved falls straight to ``USER``. Duck-typed on ``guild`` rather
    than ``isinstance(actor, discord.Member)`` so a test double only needs to
    look like a member, not subclass a real discord.py type.
    """
    if configured_label is not None:
        return configured_label, configured_emoji

    if getattr(actor, "bot", False):
        return BOT

    if not hasattr(actor, "guild"):
        return USER

    if actor.id == actor.guild.owner_id:
        return OWNER

    if await is_admin_or_superior(bot, actor):
        return ADMIN

    if await is_mod_or_superior(bot, actor):
        return MODERATOR

    return MEMBER

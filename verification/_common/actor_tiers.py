# ---------------------------------------------------------------------------
# GENERATED FILE -- DO NOT EDIT.
#
# Vendored from common/actor_tiers.py by tools/sync_common.py.
# Edit the source file and re-run the script; CI rejects drift.
# ---------------------------------------------------------------------------

"""Dynamic actor display: what badge an actor's case gets when nobody configured one.

The label and the emoji answer different questions, and are resolved
independently. The label names what the field represents -- an action type may
pin it to something fixed (Topics wants every request filed under "Requester",
say), regardless of who the actor turns out to be. The emoji says what tier
that actor actually holds right now: an automated action reads as the bot, a
human's badge follows the same owner/admin/mod/member ladder Red itself uses
for permission checks. A fixed label does not make the emoji fixed too -- an
admin editing their own message should still show the admin badge next to
whatever label the action type chose for that field.

Only when an action type configures *both* does nothing else run: that is the
one case cheap enough to skip Red's admin/mod lookups entirely.

This lives here, not in ``modlog_render``, because the ladder needs ``bot`` and is
inherently ``async`` (``is_admin_or_superior``/``is_mod_or_superior`` hit Red's admin
and mod role config), while ``build_case_embed`` is deliberately synchronous and
takes only primitives. Callers resolve a label/emoji pair here first, then pass the
result into ``build_case_embed`` like any other string.
"""

from __future__ import annotations

import discord

from redbot.core.utils.mod import is_admin_or_superior, is_mod_or_superior

# We moved all of these to just say "Actor" beecause, while we can resolve custom names,
# we decided that it's weird seeing different labels in different posts depending on
# who initiated it Admin in one log and Moderator in the next.
#
# Instead, we will refer to *everyone* as an Actor, and let the emoji dictate who they
# are to us, what their rank is, etc. This way we always know the Actor is labeled Actor.
#
# Individual cogs can still override this.

BOT = ("Actor", "🤖")
OWNER = ("Actor", "👑")
ADMIN = ("Actor", "⚔️")
MODERATOR = ("Actor", "🛡️")
MEMBER = ("Actor", "👤")
USER = ("Actor", "👤")


async def _dynamic_tier(
    bot, actor: discord.Member | discord.User | int | None
) -> tuple[str, str]:
    """The (label, emoji) pair for the actor's real standing in the guild.

    Only a live guild member carries roles and permissions to check against; a
    ``discord.User`` (someone who has since left the guild) or a bare ID that
    never resolved falls straight to ``USER``. Duck-typed on ``guild`` rather
    than ``isinstance(actor, discord.Member)`` so a test double only needs to
    look like a member, not subclass a real discord.py type.
    """
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


async def resolve_actor_display(
    bot,
    actor: discord.Member | discord.User | int | None,
    *,
    configured_label: str | None,
    configured_emoji: str | None,
) -> tuple[str, str]:
    """The (label, emoji) pair an actor's case field should use.

    A configured label and a configured emoji are independent overrides.
    Supplying both short-circuits everything else -- no Red API calls happen
    at all in that case, so a cog that always wants "Requester"/🙋 never pays
    for a permission lookup it doesn't need. Supplying only one still runs the
    dynamic tier lookup to fill in the other, since a fixed label (e.g. an
    action type that always calls its actor "Member") says nothing about
    which badge that actor's real tier should show.
    """
    if configured_label is not None and configured_emoji is not None:
        return configured_label, configured_emoji

    tier_label, tier_emoji = await _dynamic_tier(bot, actor)

    return (
        configured_label if configured_label is not None else tier_label,
        configured_emoji if configured_emoji is not None else tier_emoji,
    )

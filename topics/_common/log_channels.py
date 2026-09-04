# ---------------------------------------------------------------------------
# GENERATED FILE -- DO NOT EDIT.
#
# Vendored from common/log_channels.py by tools/sync_common.py.
# Edit the source file and re-run the script; CI rejects drift.
# ---------------------------------------------------------------------------

"""Shared per-guild channel routing for log/case-posting cogs.

Every cog that posts moderation-relevant activity somewhere (`modlog`, `audit`,
`moderation`) stores its destination channels under one guild Config key,
``log_channels``, shaped like::

    {"categories": {"adminlog": None, "modlog": None, "memberlog": None},
     "events": {}}

Resolution for a given event/action type checks ``events`` first (a specific
override), then ``categories`` (the type's broader bucket), and leaves any
further fallback -- Red core's single modlog channel, or nothing at all -- to
the caller, since that differs cog to cog.

Each cog carries its own vendored copy of this module (see
``tools/sync_common.py``), so these are plain functions operating on whatever
Config group the caller passes in, not a class holding cog-specific state.
"""

from __future__ import annotations

from typing import List

from discord import Guild, TextChannel

#: The three category buckets every cog's events fall back to.
CATEGORIES = ("adminlog", "modlog", "memberlog")

DEFAULT_LOG_CHANNELS = {"categories": {category: None for category in CATEGORIES}, "events": {}}


async def resolve_channel(
    guild: Guild,
    log_channels_group,
    *,
    action_type: str,
    category: str | None,
) -> TextChannel | None:
    """The channel ``action_type`` should post to, or None if nothing resolves.

    Checks a per-event override first, then ``category``'s default channel.
    Neither resolving is not itself an error -- the caller decides what, if
    anything, to fall back to beyond this.
    """
    channel_id = await log_channels_group.get_raw("events", action_type, default=None)

    if channel_id is None and category is not None:
        channel_id = await log_channels_group.get_raw("categories", category, default=None)

    return guild.get_channel(channel_id) if channel_id else None


async def set_event_channel(log_channels_group, action_type: str, channel_id: int | None) -> None:
    """Set (or clear, with ``channel_id=None``) the override for one event type."""
    await log_channels_group.set_raw("events", action_type, value=channel_id)


async def set_category_channel(log_channels_group, category: str, channel_id: int | None) -> None:
    """Set (or clear, with ``channel_id=None``) one category's default channel."""
    await log_channels_group.set_raw("categories", category, value=channel_id)


async def missing_send_permissions(guild: Guild, channel: TextChannel) -> List[str]:
    """Which of the permissions a log channel needs are missing, if any."""
    perms = channel.permissions_for(guild.me)
    return [
        name
        for name, has in (("Send Messages", perms.send_messages), ("Embed Links", perms.embed_links))
        if not has
    ]

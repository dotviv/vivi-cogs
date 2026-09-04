"""common/log_channels.py: the shared category/event channel resolver."""

from __future__ import annotations

import unittest

from common.log_channels import (
    missing_send_permissions,
    resolve_channel,
    set_category_channel,
    set_event_channel,
)

from tests.helpers import FakeGroup, FakeGuild, RecordingChannel


class LogChannelsTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.guild = FakeGuild()
        self.data: dict = {}
        self.writes: list = []
        self.group = FakeGroup(self.data, "log_channels", self.writes)


class TestResolveChannel(LogChannelsTestCase):
    async def test_nothing_configured_resolves_to_none(self):
        resolved = await resolve_channel(self.guild, self.group, action_type="warn", category="modlog")

        self.assertIsNone(resolved)

    async def test_category_default_is_used(self):
        channel = RecordingChannel(id=1)
        self.guild.channels[1] = channel
        await set_category_channel(self.group, "modlog", 1)

        resolved = await resolve_channel(self.guild, self.group, action_type="warn", category="modlog")

        self.assertIs(resolved, channel)

    async def test_event_override_is_used(self):
        channel = RecordingChannel(id=2)
        self.guild.channels[2] = channel
        await set_event_channel(self.group, "warn", 2)

        resolved = await resolve_channel(self.guild, self.group, action_type="warn", category="modlog")

        self.assertIs(resolved, channel)

    async def test_event_override_wins_over_category(self):
        category_channel = RecordingChannel(id=1)
        event_channel = RecordingChannel(id=2)
        self.guild.channels[1] = category_channel
        self.guild.channels[2] = event_channel
        await set_category_channel(self.group, "modlog", 1)
        await set_event_channel(self.group, "warn", 2)

        resolved = await resolve_channel(self.guild, self.group, action_type="warn", category="modlog")

        self.assertIs(resolved, event_channel)

    async def test_no_category_given_only_checks_the_event_override(self):
        await set_event_channel(self.group, "warn", 1)

        resolved = await resolve_channel(self.guild, self.group, action_type="warn", category=None)

        self.assertIsNone(resolved)  # channel 1 was never registered on the guild

    async def test_a_channel_id_that_no_longer_resolves_is_treated_as_unset(self):
        """The channel may have been deleted since it was configured."""
        await set_category_channel(self.group, "modlog", 12345)

        resolved = await resolve_channel(self.guild, self.group, action_type="warn", category="modlog")

        self.assertIsNone(resolved)


class TestSetters(LogChannelsTestCase):
    async def test_set_event_channel_clears_with_none(self):
        await set_event_channel(self.group, "warn", 1)
        await set_event_channel(self.group, "warn", None)

        stored = await self.group.get_raw("events", "warn", default="missing")
        self.assertIsNone(stored)

    async def test_set_category_channel_clears_with_none(self):
        await set_category_channel(self.group, "modlog", 1)
        await set_category_channel(self.group, "modlog", None)

        stored = await self.group.get_raw("categories", "modlog", default="missing")
        self.assertIsNone(stored)


class TestMissingSendPermissions(LogChannelsTestCase):
    async def test_no_missing_permissions_is_an_empty_list(self):
        channel = RecordingChannel(id=1)

        missing = await missing_send_permissions(self.guild, channel)

        self.assertEqual(missing, [])

    async def test_reports_each_missing_permission_by_name(self):
        class NoPermsChannel(RecordingChannel):
            def permissions_for(self, member):
                import types

                return types.SimpleNamespace(send_messages=False, embed_links=False)

        missing = await missing_send_permissions(self.guild, NoPermsChannel(id=1))

        self.assertEqual(missing, ["Send Messages", "Embed Links"])


if __name__ == "__main__":
    unittest.main()

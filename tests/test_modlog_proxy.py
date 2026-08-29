"""ModLogProxy: resolution, registration replay, and the core fallback."""

from __future__ import annotations

import unittest

import discord

import common.modlog_proxy as proxy_module
import modlog.modlog as modlog_module
from common.modlog_proxy import CaseRef, ModLogProxy
from modlog.modlog import ModLog

from tests.helpers import (
    FakeBot,
    FakeCoreModlog,
    FakeGuild,
    RecordingChannel,
    make_modlog_cog,
)

ACTION_TYPES = (
    {"type": "warn", "name": "Warning", "color": discord.Colour.yellow(), "emoji": "⚠️"},
    {"type": "kick", "name": "Kick", "color": discord.Colour.yellow(), "emoji": "🦶"},
)


class OwningCog:
    """Stands in for Moderation, Quarantine, and friends."""

    qualified_name = "Owner"

    def __init__(self, bot):
        self.bot = bot


class RecordingContext:
    """Captures what a command replied with."""

    author = None

    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content if content is not None else kwargs)


class ProxyTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = FakeBot()
        self.guild = FakeGuild()
        self.core = FakeCoreModlog()

        self._original_core = proxy_module.core_modlog
        self._original_get_channel = modlog_module.modlog.get_modlog_channel
        proxy_module.core_modlog = self.core

        async def get_modlog_channel(guild):
            return None

        modlog_module.modlog.get_modlog_channel = get_modlog_channel

        self.cog = OwningCog(self.bot)
        self.proxy = ModLogProxy(self.cog, action_types=ACTION_TYPES)

    def tearDown(self) -> None:
        proxy_module.core_modlog = self._original_core
        modlog_module.modlog.get_modlog_channel = self._original_get_channel

    def load_modlog(self):
        cog = make_modlog_cog(self.bot)
        self.bot.cogs["ModLog"] = cog
        return cog


class TestResolution(ProxyTestCase):
    def test_reports_unavailable_without_the_cog(self):
        self.assertFalse(self.proxy.available)
        self.assertFalse(self.proxy.supports_attachments)

    def test_tracks_the_live_cog_rather_than_caching(self):
        """An import would pin the old class across a reload; get_cog cannot."""
        self.load_modlog()
        self.assertTrue(self.proxy.available)

        self.bot.cogs.pop("ModLog")
        self.assertFalse(self.proxy.available)

    def test_attachments_require_the_full_modlog(self):
        """Core modlog has no way to attach a quarantine transcript."""
        self.load_modlog()
        self.assertTrue(self.proxy.supports_attachments)


class TestRegistrationReplay(ProxyTestCase):
    async def test_refresh_pushes_to_both_backends(self):
        modlog = self.load_modlog()
        await self.proxy.refresh()

        self.assertIsNotNone(modlog.action_type("warn"))
        self.assertIsNotNone(modlog.action_type("kick"))
        self.assertEqual(set(self.core.registered), {"warn", "kick"})

    async def test_replay_survives_cores_duplicate_error(self):
        """register_casetype raises when values are unchanged, so an unguarded
        replay would throw on every load after the first."""
        self.load_modlog()
        await self.proxy.refresh()
        await self.proxy.refresh()  # must not raise

    async def test_reload_of_modlog_is_repaired_by_on_cog_add(self):
        """Registrations live on the ModLog instance, so a reload drops them."""
        await self.proxy.refresh()

        reloaded = self.load_modlog()
        self.assertIsNone(reloaded.action_type("warn"))

        await self.proxy.on_cog_add(reloaded)
        self.assertIsNotNone(reloaded.action_type("warn"))

    async def test_unrelated_cogs_are_ignored(self):
        modlog = self.load_modlog()
        await self.proxy.refresh()
        before = dict(modlog._action_types)

        other = OwningCog(self.bot)
        other.qualified_name = "SomethingElse"
        await self.proxy.on_cog_add(other)

        self.assertEqual(modlog._action_types, before)


class TestCaseRouting(ProxyTestCase):
    async def test_prefers_modlog_and_returns_a_local_caseref(self):
        self.load_modlog()
        await self.proxy.refresh()

        ref = await self.proxy.create_case(
            self.guild, action_type="warn", target=222, moderator=111, reason="spamming"
        )

        self.assertIsInstance(ref, CaseRef)
        self.assertEqual(ref.source, "modlog")
        self.assertEqual(ref.case_number, 1)
        self.assertEqual(ref.action_name, "Warning")
        self.assertEqual(self.core.created, [])

    async def test_caseref_is_not_modlogs_case(self):
        """Each cog vendors its own copy of the helpers, so ModLog's classes are
        foreign types here and must never cross the boundary."""
        self.load_modlog()
        await self.proxy.refresh()

        ref = await self.proxy.create_case(
            self.guild, action_type="warn", target=222, moderator=111, reason="x"
        )

        self.assertNotIsInstance(ref, ModLog.Case)

    async def test_falls_back_to_core_without_modlog(self):
        await self.proxy.refresh()

        ref = await self.proxy.create_case(
            self.guild, action_type="warn", target=222, moderator=111, reason="x"
        )

        self.assertEqual(self.core.created, ["warn"])
        self.assertEqual(ref.source, "core")
        self.assertEqual(ref.case_number, 42)

    async def test_display_resolves_from_declarations_without_modlog(self):
        """confirm_action and summaries still need a readable name."""
        ref = await self.proxy.create_case(
            self.guild, action_type="warn", target=222, moderator=111, reason="x"
        )

        self.assertEqual(ref.action_name, "Warning")


class TestFallbackPolicy(ProxyTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.private = ModLogProxy(
            self.cog, action_types=ACTION_TYPES, core_fallback=False
        )

    async def test_refuses_to_register_core_casetypes(self):
        await self.private.refresh()

        self.assertEqual(self.core.registered, [])

    async def test_records_nothing_rather_than_leaking_to_core(self):
        """Core's [p]case and [p]casesfor carry no permission check, so a core
        case would let any member deanonymise a topic-change requester."""
        result = await self.private.create_case(
            self.guild, action_type="warn", target=222, moderator=111, reason="x"
        )

        self.assertIsNone(result)
        self.assertEqual(self.core.created, [])

    async def test_still_uses_modlog_when_it_is_present(self):
        self.load_modlog()
        await self.private.refresh()

        ref = await self.private.create_case(
            self.guild, action_type="warn", target=222, moderator=111, reason="x"
        )

        self.assertIsNotNone(ref)
        self.assertEqual(ref.source, "modlog")


class TestCoreFailureModes(ProxyTestCase):
    """Core has three distinct ways to not produce a case. None may propagate."""

    async def test_disabled_casetype_returns_none(self):
        self.core.create_result = None

        result = await self.proxy.create_case(
            self.guild, action_type="warn", target=222, moderator=111, reason="x"
        )

        self.assertIsNone(result)

    async def test_unregistered_casetype_returns_none(self):
        self.core.create_result = "value_error"

        result = await self.proxy.create_case(
            self.guild, action_type="warn", target=222, moderator=111, reason="x"
        )

        self.assertIsNone(result)

    async def test_bot_as_target_returns_none(self):
        self.core.create_result = "runtime_error"

        result = await self.proxy.create_case(
            self.guild, action_type="warn", target=222, moderator=111, reason="x"
        )

        self.assertIsNone(result)


class TestEvents(ProxyTestCase):
    async def test_posts_a_case_shaped_embed_without_a_case_number(self):
        self.core.channel = RecordingChannel()

        posted = await self.proxy.log_event(
            self.guild, action_type="warn", target=222, reason="failed a captcha"
        )

        self.assertTrue(posted)
        self.assertEqual(len(self.core.channel.sent), 1)

        names = [f.name for f in self.core.channel.sent[0]["embed"].fields]
        self.assertNotIn("Case:", names)
        self.assertEqual(names[0], "Type:")
        self.assertEqual(names[-1], "Reason:")

    async def test_reports_failure_without_a_channel(self):
        self.core.channel = None

        posted = await self.proxy.log_event(
            self.guild, action_type="warn", target=222, reason="x"
        )

        self.assertFalse(posted)


class TestSummaries(ProxyTestCase):
    async def test_unrecorded_action_falls_back_to_text(self):
        """The action happened even if nothing recorded it; say so."""
        ctx = RecordingContext()

        await self.proxy.send_case_action_summary(ctx, None, note="Channel created.")

        self.assertEqual(len(ctx.sent), 1)
        self.assertIsInstance(ctx.sent[0], str)
        self.assertIn("Channel created.", ctx.sent[0])

    async def test_note_goes_in_the_description_not_a_field(self):
        """Reason has to stay the last field."""
        ctx = RecordingContext()
        ref = CaseRef(
            action_type="warn",
            action_name="Warning",
            action_color=discord.Colour.yellow(),
            action_emoji="⚠️",
            target=222,
            reason="spamming",
            timestamp=0.0,
            case_number=5,
        )

        await self.proxy.send_case_action_summary(ctx, ref, note="Extra context.")

        embed = ctx.sent[0]["embed"]
        self.assertEqual(embed.description, "Extra context.")
        self.assertEqual([f.name for f in embed.fields][-1], "Reason:")


if __name__ == "__main__":
    unittest.main()

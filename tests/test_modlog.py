"""The ModLog cog: registry, case storage, and rendering."""

from __future__ import annotations

import unittest

import discord

import modlog.modlog as modlog_module
from common.modlog_render import build_case_embed
from modlog.modlog import ModLog, UnknownActionType

from tests.helpers import FakeBot, FakeGuild, RecordingChannel, make_modlog_cog

WARN = {
    "type": "warn",
    "name": "Warning",
    "color": discord.Colour.yellow(),
    "emoji": "⚠️",
}


class ModLogTestCase(unittest.IsolatedAsyncioTestCase):
    """Base that isolates the module-level modlog channel lookup."""

    def setUp(self) -> None:
        self.bot = FakeBot()
        self.guild = FakeGuild()
        self.cog = make_modlog_cog(self.bot)
        self.cog.register_action_type(**WARN)

        self._original_get_channel = modlog_module.modlog.get_modlog_channel
        self.channel = None

        async def get_modlog_channel(guild):
            return self.channel

        modlog_module.modlog.get_modlog_channel = get_modlog_channel

    def tearDown(self) -> None:
        modlog_module.modlog.get_modlog_channel = self._original_get_channel


class TestActionTypeRegistry(ModLogTestCase):
    def test_registry_is_instance_state_not_class_state(self):
        """Red reloads a cog by rebinding its class, so a class-level registry
        would survive only as a stale duplicate the new instance cannot see."""
        other = make_modlog_cog(self.bot)

        self.assertIsNotNone(self.cog.action_type("warn"))
        self.assertIsNone(other.action_type("warn"))

    def test_no_class_level_registry_remains(self):
        self.assertFalse(hasattr(ModLog, "ACTION_TYPES"))

    def test_re_registering_overwrites(self):
        """Replay re-registers on every load; it must not accumulate or fail."""
        self.cog.register_action_type(
            type="warn", name="Warn v2", color=discord.Colour.red(), emoji="!"
        )

        self.assertEqual(self.cog.action_type("warn").name, "Warn v2")

    def test_unknown_type_resolves_to_none(self):
        self.assertIsNone(self.cog.action_type("nope"))


class TestCaseSerialisation(ModLogTestCase):
    def _case(self, **overrides):
        defaults = dict(
            action_type=self.cog.action_type("warn"),
            case_number=1,
            moderator=111,
            target=222,
            reason="test",
            timestamp=0.0,
            duration=None,
        )
        defaults.update(overrides)
        return ModLog.Case(**defaults)

    def test_to_dict_succeeds_on_a_fresh_case(self):
        """channel_id and message_id were bare class annotations, which create no
        attributes, while to_dict read them unconditionally -- so every single
        create_case raised AttributeError."""
        payload = self._case().to_dict()

        self.assertIsNone(payload["channel_id"])
        self.assertIsNone(payload["message_id"])

    def test_round_trip_preserves_the_case(self):
        payload = self._case(reason="spamming").to_dict()
        rebuilt = self.cog.case_from_dict(self.guild, payload)

        self.assertEqual(rebuilt.case_number, 1)
        self.assertEqual(rebuilt.reason, "spamming")
        self.assertEqual(rebuilt.action_type.name, "Warning")

    def test_rebuilding_an_unregistered_type_raises(self):
        payload = self._case().to_dict()
        bare = make_modlog_cog(self.bot)

        with self.assertRaises(UnknownActionType):
            bare.case_from_dict(self.guild, payload)

    def test_moderator_may_be_none(self):
        """Core modlog treats the moderator as optional, and the proxy allows
        None, so ModLog must not be the one component that crashes on it."""
        payload = self._case(moderator=None).to_dict()

        self.assertIsNone(payload["moderator_id"])

        rebuilt = self.cog.case_from_dict(self.guild, payload)
        self.assertIsNone(rebuilt.moderator)

        embed = self.cog.case_embed(rebuilt)
        self.assertNotIn("Moderator:", [field.name for field in embed.fields])


class TestCreateCase(ModLogTestCase):
    async def test_numbers_cases_sequentially(self):
        first = await self.cog.create_case(
            self.guild, action_type="warn", moderator=111, target=222, reason="first"
        )
        second = await self.cog.create_case(
            self.guild, action_type="warn", moderator=111, target=222, reason="second"
        )

        self.assertEqual(first.case_number, 1)
        self.assertEqual(second.case_number, 2)
        self.assertEqual(self.cog.config.data["case_sequence"], 3)

    async def test_indexes_cases_against_the_target(self):
        await self.cog.create_case(
            self.guild, action_type="warn", moderator=111, target=222, reason="x"
        )
        await self.cog.create_case(
            self.guild, action_type="warn", moderator=111, target=222, reason="y"
        )

        self.assertEqual(self.cog.config.data["user_cases"]["222"], [1, 2])

    async def test_every_write_is_scoped_to_a_key(self):
        """The original opened the whole guild group twice per case, making each
        write O(total cases). Verification traffic turns that into a problem."""
        await self.cog.create_case(
            self.guild, action_type="warn", moderator=111, target=222, reason="x"
        )

        self.assertTrue(self.cog.config.writes)
        for path in self.cog.config.writes:
            self.assertGreaterEqual(
                len(path), 2, f"{path} rewrites an entire group"
            )

    async def test_unregistered_action_type_raises(self):
        with self.assertRaises(UnknownActionType):
            await self.cog.create_case(
                self.guild, action_type="nope", moderator=111, target=222, reason="x"
            )

    async def test_case_is_stored_even_without_a_modlog_channel(self):
        self.channel = None

        await self.cog.create_case(
            self.guild, action_type="warn", moderator=111, target=222, reason="x"
        )

        self.assertIn("1", self.cog.config.data["cases"])

    async def test_posting_persists_message_coordinates(self):
        """Without these stored, [p]reason could never edit the posted case."""
        self.channel = RecordingChannel()

        await self.cog.create_case(
            self.guild, action_type="warn", moderator=111, target=222, reason="x"
        )

        stored = self.cog.config.data["cases"]["1"]
        self.assertEqual(len(self.channel.sent), 1)
        self.assertEqual(stored["channel_id"], 444)
        self.assertEqual(stored["message_id"], 555)


class TestEmbedBuilder(unittest.TestCase):
    """The one renderer, shared by ModLog and every consuming cog."""

    def _build(self, **overrides):
        defaults = dict(
            action_name="Warning",
            action_color=discord.Colour.yellow(),
            action_emoji="⚠️",
            target=222,
            reason="because",
            timestamp=0.0,
        )
        defaults.update(overrides)
        return build_case_embed(**defaults)

    def test_case_number_produces_a_case_field(self):
        embed = self._build(case_number=7)

        self.assertEqual(embed.fields[0].name, "Case:")

    def test_omitting_the_case_number_omits_the_field(self):
        """Events render as siblings of cases, minus the number."""
        embed = self._build()

        self.assertNotIn("Case:", [field.name for field in embed.fields])
        self.assertEqual(embed.fields[0].name, "Type:")

    def test_reason_is_always_the_final_field(self):
        """[p]reason edits a posted case by index from the end."""
        for kwargs in ({}, {"case_number": 7}, {"detailed": True}, {"duration": "1h"}):
            with self.subTest(**kwargs):
                embed = self._build(**kwargs)
                self.assertEqual(embed.fields[-1].name, "Reason:")

    def test_moderator_is_omitted_when_absent(self):
        embed = self._build()

        self.assertNotIn("Moderator:", [field.name for field in embed.fields])

    def test_detailed_exposes_raw_ids(self):
        embed = self._build(case_number=7, moderator=111, detailed=True)
        names = [field.name for field in embed.fields]

        self.assertIn("Target ID:", names)
        self.assertIn("Moderator ID:", names)

    def test_duration_shown_only_when_set_or_detailed(self):
        names = [field.name for field in self._build().fields]
        self.assertNotIn("Duration:", names)

        names = [field.name for field in self._build(duration="1h").fields]
        self.assertIn("Duration:", names)

        names = [field.name for field in self._build(detailed=True).fields]
        self.assertIn("Duration:", names)


if __name__ == "__main__":
    unittest.main()

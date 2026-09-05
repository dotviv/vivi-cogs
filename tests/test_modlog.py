"""The ModLog cog: registry, case storage, and rendering."""

from __future__ import annotations

import unittest

import discord

import modlog.modlog as modlog_module
from common.modlog_render import build_case_embed
from modlog.modlog import ModLog, UnknownActionType

from tests.helpers import FakeBot, FakeGuild, RecordingChannel, make_modlog_cog


class FakeCommandContext:
    def __init__(self, *, guild) -> None:
        self.guild = guild
        self.sent: list = []

    async def embed_colour(self):
        return discord.Colour.blurple()

    async def send(self, content=None, **kwargs):
        self.sent.append(content if content is not None else kwargs)

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

    def test_registered_type_defaults_to_modlog_category(self):
        self.assertEqual(self.cog.action_type("warn").category, "modlog")

    def test_unset_display_fields_default(self):
        registered = self.cog.action_type("warn")

        self.assertEqual(registered.target_label, "Target")
        self.assertEqual(registered.target_emoji, "🎯")
        self.assertEqual(registered.actor_label, "Actor")
        self.assertEqual(registered.actor_emoji, "🛡️")
        self.assertFalse(registered.requires_reason)

    def test_custom_display_fields_round_trip(self):
        """A registering cog's target/actor labels and emoji, and whether it
        requires a reason, must actually reach the registry -- register_action_type
        used to silently drop everything but type/name/color/emoji/category."""
        self.cog.register_action_type(
            type="topic_change",
            name="Topic Change Request",
            color=discord.Colour.blue(),
            emoji="💬",
            requires_reason=True,
            target_label="Requester",
            target_emoji="🙋",
            actor_label="Requester",
            actor_emoji="🙋",
        )

        registered = self.cog.action_type("topic_change")

        self.assertTrue(registered.requires_reason)
        self.assertEqual(registered.target_label, "Requester")
        self.assertEqual(registered.target_emoji, "🙋")
        self.assertEqual(registered.actor_label, "Requester")
        self.assertEqual(registered.actor_emoji, "🙋")

    def test_explicit_category_round_trips(self):
        self.cog.register_action_type(
            type="raid_alert", name="Raid Alert", color=discord.Colour.red(), emoji="🚨", category="adminlog"
        )

        self.assertEqual(self.cog.action_type("raid_alert").category, "adminlog")

    def test_unknown_category_is_coerced_to_default(self):
        self.cog.register_action_type(
            type="mystery", name="Mystery", color=discord.Colour.blue(), emoji=None, category="nonsense"
        )

        self.assertEqual(self.cog.action_type("mystery").category, "modlog")


class TestCaseSerialisation(ModLogTestCase):
    def _case(self, **overrides):
        defaults = dict(
            action_type=self.cog.action_type("warn"),
            case_number=1,
            actor=111,
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

    def test_actor_may_be_none(self):
        """Core modlog treats the actor as optional, and the proxy allows
        None, so ModLog must not be the one component that crashes on it."""
        payload = self._case(actor=None).to_dict()

        self.assertIsNone(payload["actor_id"])

        rebuilt = self.cog.case_from_dict(self.guild, payload)
        self.assertIsNone(rebuilt.actor)

        embed = self.cog.case_embed(rebuilt)
        self.assertNotIn("Actor:", [field.name for field in embed.fields])

    def test_target_may_be_none(self):
        """An actor-only action -- e.g. warning a whole channel -- has no
        single member it happened to."""
        payload = self._case(target=None).to_dict()

        self.assertIsNone(payload["target_id"])

        rebuilt = self.cog.case_from_dict(self.guild, payload)
        self.assertIsNone(rebuilt.target)

        embed = self.cog.case_embed(rebuilt)
        self.assertNotIn("Target:", [field.name for field in embed.fields])


class TestCreateCase(ModLogTestCase):
    async def test_numbers_cases_sequentially(self):
        first = await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, reason="first"
        )
        second = await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, reason="second"
        )

        self.assertEqual(first.case_number, 1)
        self.assertEqual(second.case_number, 2)
        self.assertEqual(self.cog.config.data["case_sequence"], 3)

    async def test_indexes_cases_against_the_target(self):
        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, reason="x"
        )
        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, reason="y"
        )

        self.assertEqual(self.cog.config.data["user_cases"]["222"], [1, 2])

    async def test_indexes_cases_against_the_actor(self):
        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, reason="x"
        )
        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=333, reason="y"
        )

        self.assertEqual(self.cog.config.data["actor_cases"]["111"], [1, 2])

    async def test_targetless_case_is_stored_but_not_indexed_by_target(self):
        """An actor-only action -- e.g. warning a whole channel -- still
        gets a case, just not filed under anyone's target-history."""
        await self.cog.create_case(
            self.guild,
            action_type="warn",
            actor=111,
            target=None,
            reason="channel-wide warning",
        )

        self.assertIn("1", self.cog.config.data["cases"])
        self.assertNotIn("user_cases", self.cog.config.data)
        self.assertEqual(self.cog.config.data["actor_cases"]["111"], [1])

    async def test_case_with_no_actor_is_not_indexed_by_actor(self):
        await self.cog.create_case(
            self.guild, action_type="warn", actor=None, target=222, reason="x"
        )

        self.assertNotIn("actor_cases", self.cog.config.data)
        self.assertEqual(self.cog.config.data["user_cases"]["222"], [1])

    async def test_every_write_is_scoped_to_a_key(self):
        """The original opened the whole guild group twice per case, making each
        write O(total cases). Verification traffic turns that into a problem."""
        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, reason="x"
        )

        self.assertTrue(self.cog.config.writes)
        for path in self.cog.config.writes:
            self.assertGreaterEqual(
                len(path), 2, f"{path} rewrites an entire group"
            )

    async def test_unregistered_action_type_raises(self):
        with self.assertRaises(UnknownActionType):
            await self.cog.create_case(
                self.guild, action_type="nope", actor=111, target=222, reason="x"
            )

    async def test_case_is_stored_even_without_a_modlog_channel(self):
        self.channel = None

        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, reason="x"
        )

        self.assertIn("1", self.cog.config.data["cases"])

    async def test_posting_persists_message_coordinates(self):
        """Without these stored, [p]reason could never edit the posted case."""
        self.channel = RecordingChannel()

        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, reason="x"
        )

        stored = self.cog.config.data["cases"]["1"]
        self.assertEqual(len(self.channel.sent), 1)
        self.assertEqual(stored["channel_id"], 444)
        self.assertEqual(stored["message_id"], 555)


class TestChannelRouting(ModLogTestCase):
    """_post_case's resolution order: event override -> category -> core."""

    async def test_falls_back_to_core_channel_when_nothing_configured(self):
        self.channel = RecordingChannel()

        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, reason="x"
        )

        self.assertEqual(len(self.channel.sent), 1)

    async def test_category_channel_is_preferred_over_core(self):
        self.channel = RecordingChannel()  # core fallback -- must not receive anything
        category_channel = RecordingChannel(id=501)
        self.guild.channels[501] = category_channel
        self.cog.config.data["log_channels"] = {"categories": {"modlog": 501}, "events": {}}

        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, reason="x"
        )

        self.assertEqual(len(category_channel.sent), 1)
        self.assertEqual(len(self.channel.sent), 0)

    async def test_event_override_wins_over_category(self):
        category_channel = RecordingChannel(id=501)
        event_channel = RecordingChannel(id=502)
        self.guild.channels[501] = category_channel
        self.guild.channels[502] = event_channel
        self.cog.config.data["log_channels"] = {
            "categories": {"modlog": 501},
            "events": {"warn": 502},
        }

        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, reason="x"
        )

        self.assertEqual(len(event_channel.sent), 1)
        self.assertEqual(len(category_channel.sent), 0)


class TestChannelCommands(ModLogTestCase):
    async def test_setting_a_category_channel(self):
        channel = RecordingChannel(id=501)
        self.guild.channels[501] = channel
        ctx = FakeCommandContext(guild=self.guild)

        await self.cog._modlogchannels_category(ctx, "modlog", channel)

        stored = await self.cog.config.guild(self.guild).log_channels.get_raw(
            "categories", "modlog", default=None
        )
        self.assertEqual(stored, 501)
        self.assertIn("set to", ctx.sent[-1])

    async def test_unknown_category_is_rejected(self):
        ctx = FakeCommandContext(guild=self.guild)

        await self.cog._modlogchannels_category(ctx, "nonsense", RecordingChannel())

        self.assertIn("Unknown category", ctx.sent[-1])

    async def test_setting_an_event_channel(self):
        channel = RecordingChannel(id=502)
        self.guild.channels[502] = channel
        ctx = FakeCommandContext(guild=self.guild)

        await self.cog._modlogchannels_event(ctx, "warn", channel)

        stored = await self.cog.config.guild(self.guild).log_channels.get_raw(
            "events", "warn", default=None
        )
        self.assertEqual(stored, 502)

    async def test_unregistered_event_type_is_rejected(self):
        ctx = FakeCommandContext(guild=self.guild)

        await self.cog._modlogchannels_event(ctx, "nope", RecordingChannel())

        self.assertIn("Unknown action type", ctx.sent[-1])

    async def test_settings_renders_categories_and_events(self):
        category_channel = RecordingChannel(id=501)
        event_channel = RecordingChannel(id=502)
        self.guild.channels[501] = category_channel
        self.guild.channels[502] = event_channel
        log_channels = self.cog.config.guild(self.guild).log_channels
        await log_channels.set_raw("categories", "modlog", value=501)
        await log_channels.set_raw("events", "warn", value=502)
        ctx = FakeCommandContext(guild=self.guild)

        await self.cog._modlogchannels_settings(ctx)

        embed = ctx.sent[-1]["embed"]
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("<#501>", fields["Categories"])
        self.assertIn("<#502>", fields["Event overrides"])


class TestCasesSince(ModLogTestCase):
    async def test_filters_by_timestamp(self):
        old = await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, reason="old"
        )
        self.cog.config.data["cases"][str(old.case_number)]["timestamp"] = 1000.0

        new = await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, reason="new"
        )
        self.cog.config.data["cases"][str(new.case_number)]["timestamp"] = 5000.0

        recent = await self.cog.cases_since(self.guild, 4000.0)

        self.assertEqual([c.case_number for c in recent], [new.case_number])

    async def test_skips_cases_from_unloaded_cogs(self):
        """A digest can't name a case whose owning cog isn't loaded, so it is
        skipped rather than raising -- same policy as CasePageEmbedProvider."""
        case = ModLog.Case(
            action_type=self.cog.action_type("warn"),
            case_number=1,
            actor=111,
            target=222,
            reason="test",
            timestamp=0.0,
            duration=None,
        )
        bare = make_modlog_cog(self.bot)
        bare.config.data["cases"] = {"1": case.to_dict()}

        recent = await bare.cases_since(self.guild, 0.0)

        self.assertEqual(recent, [])


class TestEmbedBuilder(unittest.TestCase):
    """The one renderer, shared by ModLog and every consuming cog."""

    def _build(self, **overrides):
        defaults = dict(
            action_name="Warning",
            action_color=discord.Colour.yellow(),
            action_emoji="⚠️",
            target_label="Target",
            target=222,
            actor_label="Actor",
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
        for kwargs in (
            {},
            {"case_number": 7},
            {"detailed": True},
            {"duration": "1h"},
            {"target": None},
        ):
            with self.subTest(**kwargs):
                embed = self._build(**kwargs)
                self.assertEqual(embed.fields[-1].name, "Reason:")

    def test_omitting_the_target_omits_the_field(self):
        """A global or actor-only action has no single member it happened
        to -- shown as absent entirely, not as unavailable."""
        embed = self._build(target=None)

        self.assertNotIn("Target:", [field.name for field in embed.fields])
        self.assertNotIn("Target ID:", [field.name for field in embed.fields])

    def test_omitting_the_target_still_omits_ids_when_detailed(self):
        embed = self._build(target=None, detailed=True)

        self.assertNotIn("Target ID:", [field.name for field in embed.fields])

    def test_actor_is_omitted_when_absent(self):
        embed = self._build()

        self.assertNotIn("Actor:", [field.name for field in embed.fields])

    def test_detailed_exposes_raw_ids(self):
        embed = self._build(case_number=7, actor=111, detailed=True)
        names = [field.name for field in embed.fields]

        self.assertIn("Target ID:", names)
        self.assertIn("Actor ID:", names)

    def test_custom_labels_and_emoji_replace_the_defaults(self):
        embed = self._build(
            target_label="Requester",
            target_emoji="🙋",
            actor=111,
            actor_label="Approver",
            actor_emoji="✅",
        )
        names = [field.name for field in embed.fields]

        self.assertIn("Requester:", names)
        self.assertIn("Approver:", names)
        self.assertNotIn("Target:", names)
        self.assertNotIn("Actor:", names)

    def test_duration_shown_only_when_set_or_detailed(self):
        names = [field.name for field in self._build().fields]
        self.assertNotIn("Duration:", names)

        names = [field.name for field in self._build(duration="1h").fields]
        self.assertIn("Duration:", names)

        names = [field.name for field in self._build(detailed=True).fields]
        self.assertIn("Duration:", names)


if __name__ == "__main__":
    unittest.main()

"""The ModLog cog: registry, case storage, and rendering."""

from __future__ import annotations

import unittest

import discord

import modlog.modlog as modlog_module
from common.modlog_render import build_case_embed, reason_field
from modlog.modlog import ModLog, UnknownActionType

from tests.helpers import FakeBot, FakeGuild, FakeMember, RecordingChannel, make_modlog_cog


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
        """actor_label/actor_emoji default to None -- the sentinel that tells
        resolve_actor_display to fall back to dynamic tier resolution rather
        than a fixed label. target_label/target_emoji are never dynamic, so
        those still default to a concrete value."""
        registered = self.cog.action_type("warn")

        self.assertEqual(registered.target_label, "Target")
        self.assertEqual(registered.target_emoji, "🎯")
        self.assertIsNone(registered.actor_label)
        self.assertIsNone(registered.actor_emoji)
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
            fields=reason_field("test"),
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
        payload = self._case(fields=reason_field("spamming")).to_dict()
        rebuilt = self.cog.case_from_dict(self.guild, payload)

        self.assertEqual(rebuilt.case_number, 1)
        self.assertEqual(rebuilt.fields, reason_field("spamming"))
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

    def test_actor_label_and_emoji_round_trip(self):
        """The frozen tier survives storage -- to_dict/from_dict must not
        silently drop it back to the static default."""
        payload = self._case(actor_label="Admin", actor_emoji="⚔️").to_dict()

        self.assertEqual(payload["actor_label"], "Admin")
        self.assertEqual(payload["actor_emoji"], "⚔️")

        rebuilt = self.cog.case_from_dict(self.guild, payload)
        self.assertEqual(rebuilt.actor_label, "Admin")
        self.assertEqual(rebuilt.actor_emoji, "⚔️")

    def test_case_embed_renders_the_frozen_label_not_the_registry(self):
        """case_embed must read actor_label/actor_emoji off the case itself,
        not action_type -- action_type's are unconfigured (None) for "warn",
        which would break embed rendering if read directly."""
        case = self._case(actor_label="Owner", actor_emoji="👑")

        embed = self.cog.case_embed(case)
        names = {field.name: field.value for field in embed.fields}

        self.assertIn("Owner:", names)

    def test_old_stored_case_without_a_frozen_tier_gets_the_static_default(self):
        """A case stored before dynamic tiers existed has no actor_label/
        actor_emoji keys at all -- from_dict must not raise, and must not
        guess; it falls back to the old static default."""
        payload = self._case().to_dict()
        del payload["actor_label"]
        del payload["actor_emoji"]

        rebuilt = self.cog.case_from_dict(self.guild, payload)

        self.assertEqual(rebuilt.actor_label, "Actor")
        self.assertEqual(rebuilt.actor_emoji, "🛡️")

    def test_old_stored_case_without_fields_synthesizes_a_reason_field(self):
        """A case stored before dynamic fields existed has a plain "reason"
        string and no "fields" key at all -- from_dict must not raise, and
        must recover the reason as the one field it would have been."""
        payload = self._case().to_dict()
        del payload["fields"]
        payload["reason"] = "spamming"

        rebuilt = self.cog.case_from_dict(self.guild, payload)

        self.assertEqual(rebuilt.fields, reason_field("spamming"))

    def test_old_stored_case_with_no_reason_and_no_fields_gets_empty_fields(self):
        payload = self._case().to_dict()
        del payload["fields"]
        payload["reason"] = None

        rebuilt = self.cog.case_from_dict(self.guild, payload)

        self.assertEqual(rebuilt.fields, [])


class TestCreateCase(ModLogTestCase):
    async def test_numbers_cases_sequentially(self):
        first = await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, fields=reason_field("first")
        )
        second = await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, fields=reason_field("second")
        )

        self.assertEqual(first.case_number, 1)
        self.assertEqual(second.case_number, 2)
        self.assertEqual(self.cog.config.data["case_sequence"], 3)

    async def test_indexes_cases_against_the_target(self):
        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, fields=reason_field("x")
        )
        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, fields=reason_field("y")
        )

        self.assertEqual(self.cog.config.data["user_cases"]["222"], [1, 2])

    async def test_indexes_cases_against_the_actor(self):
        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, fields=reason_field("x")
        )
        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=333, fields=reason_field("y")
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
            fields=reason_field("channel-wide warning"),
        )

        self.assertIn("1", self.cog.config.data["cases"])
        self.assertNotIn("user_cases", self.cog.config.data)
        self.assertEqual(self.cog.config.data["actor_cases"]["111"], [1])

    async def test_case_with_no_actor_is_not_indexed_by_actor(self):
        await self.cog.create_case(
            self.guild, action_type="warn", actor=None, target=222, fields=reason_field("x")
        )

        self.assertNotIn("actor_cases", self.cog.config.data)
        self.assertEqual(self.cog.config.data["user_cases"]["222"], [1])

    async def test_every_write_is_scoped_to_a_key(self):
        """The original opened the whole guild group twice per case, making each
        write O(total cases). Verification traffic turns that into a problem."""
        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, fields=reason_field("x")
        )

        self.assertTrue(self.cog.config.writes)
        for path in self.cog.config.writes:
            self.assertGreaterEqual(
                len(path), 2, f"{path} rewrites an entire group"
            )

    async def test_unregistered_action_type_raises(self):
        with self.assertRaises(UnknownActionType):
            await self.cog.create_case(
                self.guild, action_type="nope", actor=111, target=222, fields=reason_field("x")
            )

    async def test_case_is_stored_even_without_a_modlog_channel(self):
        self.channel = None

        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, fields=reason_field("x")
        )

        self.assertIn("1", self.cog.config.data["cases"])

    async def test_posting_persists_message_coordinates(self):
        """Without these stored, [p]reason could never edit the posted case."""
        self.channel = RecordingChannel()

        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, fields=reason_field("x")
        )

        stored = self.cog.config.data["cases"]["1"]
        self.assertEqual(len(self.channel.sent), 1)
        self.assertEqual(stored["channel_id"], 444)
        self.assertEqual(stored["message_id"], 555)

    async def test_actor_tier_is_resolved_and_frozen_onto_the_case(self):
        """warn's actor_label/actor_emoji are unconfigured (None), so
        create_case resolves the actor's real tier and freezes it onto the
        Case -- a later re-render must not need to look it up again."""
        actor = FakeMember(111, "admin", self.guild)
        self.bot.admin_ids.add(111)

        case = await self.cog.create_case(
            self.guild, action_type="warn", actor=actor, target=222, fields=reason_field("x")
        )

        self.assertEqual(case.actor_label, "Admin")
        self.assertEqual(case.actor_emoji, "⚔️")
        self.assertEqual(self.cog.config.data["cases"]["1"]["actor_label"], "Admin")
        self.assertEqual(self.cog.config.data["cases"]["1"]["actor_emoji"], "⚔️")

    async def test_configured_actor_label_bypasses_dynamic_resolution(self):
        """A registered actor_label always wins, even for an actor who would
        otherwise resolve as the guild owner."""
        self.cog.register_action_type(
            type="topic_change", name="Topic Change", color=discord.Colour.blue(),
            emoji="💬", actor_label="Requester", actor_emoji="🙋",
        )
        owner = FakeMember(999, "owner", self.guild)
        self.guild.owner_id = 999

        case = await self.cog.create_case(
            self.guild, action_type="topic_change", actor=owner, fields=reason_field("x")
        )

        self.assertEqual(case.actor_label, "Requester")
        self.assertEqual(case.actor_emoji, "🙋")

    async def test_no_actor_gets_the_static_placeholder(self):
        """An unattributed/automated action with no actor at all has no tier
        to resolve -- the placeholder is never rendered anyway since
        build_case_embed omits the whole field when actor is None."""
        case = await self.cog.create_case(
            self.guild, action_type="warn", actor=None, target=222, fields=reason_field("x")
        )

        self.assertEqual(case.actor_label, "Actor")
        self.assertEqual(case.actor_emoji, "🛡️")


class TestChannelRouting(ModLogTestCase):
    """_post_case's resolution order: event override -> category -> core."""

    async def test_falls_back_to_core_channel_when_nothing_configured(self):
        self.channel = RecordingChannel()

        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, fields=reason_field("x")
        )

        self.assertEqual(len(self.channel.sent), 1)

    async def test_category_channel_is_preferred_over_core(self):
        self.channel = RecordingChannel()  # core fallback -- must not receive anything
        category_channel = RecordingChannel(id=501)
        self.guild.channels[501] = category_channel
        self.cog.config.data["log_channels"] = {"categories": {"modlog": 501}, "events": {}}

        await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, fields=reason_field("x")
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
            self.guild, action_type="warn", actor=111, target=222, fields=reason_field("x")
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
            self.guild, action_type="warn", actor=111, target=222, fields=reason_field("old")
        )
        self.cog.config.data["cases"][str(old.case_number)]["timestamp"] = 1000.0

        new = await self.cog.create_case(
            self.guild, action_type="warn", actor=111, target=222, fields=reason_field("new")
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
            fields=reason_field("test"),
            timestamp=0.0,
            duration=None,
        )
        bare = make_modlog_cog(self.bot)
        bare.config.data["cases"] = {"1": case.to_dict()}

        recent = await bare.cases_since(self.guild, 0.0)

        self.assertEqual(recent, [])


class FakeReasonMessage:
    """A posted case message, editable in place -- like RecordingChannel's
    FakeMessage, but able to fetch back and re-render its embed."""

    def __init__(self, embed):
        self.embeds = [embed]
        self.edited_embed = None

    async def edit(self, *, embed):
        self.edited_embed = embed
        self.embeds = [embed]


class FakeReasonChannel:
    """Just enough of a channel for [p]reason to fetch its posted message."""

    def __init__(self, message):
        self.message = message

    async def fetch_message(self, message_id):
        return self.message


class TestReasonCommand(ModLogTestCase):
    """[p]reason edits the stored and posted "Reason" field by name."""

    def _stored_case(self, *, fields, channel_id=None, message_id=None):
        case = ModLog.Case(
            action_type=self.cog.action_type("warn"),
            case_number=1,
            actor=111,
            target=222,
            fields=fields,
            timestamp=0.0,
            duration=None,
            channel_id=channel_id,
            message_id=message_id,
        )
        self.cog.config.data["cases"] = {"1": case.to_dict()}
        return case

    async def test_updates_a_non_last_reason_field_by_name(self):
        self._stored_case(
            fields=[
                {"name": "Note", "content": "n", "inline": False},
                {"name": "Reason", "content": "old", "inline": False},
            ]
        )
        ctx = FakeCommandContext(guild=self.guild)

        await self.cog.reason.callback(self.cog, ctx, 1, reason="new text")

        stored = self.cog.config.data["cases"]["1"]["fields"]
        self.assertEqual(
            stored,
            [
                {"name": "Note", "content": "n", "inline": False},
                {"name": "Reason", "content": "new text", "inline": False},
            ],
        )

    async def test_appends_a_reason_field_when_absent(self):
        self._stored_case(fields=[{"name": "Note", "content": "n", "inline": False}])
        ctx = FakeCommandContext(guild=self.guild)

        await self.cog.reason.callback(self.cog, ctx, 1, reason="new text")

        stored = self.cog.config.data["cases"]["1"]["fields"]
        self.assertEqual(stored[-1], {"name": "Reason", "content": "new text", "inline": False})

    async def test_live_embed_patch_targets_the_field_by_name_not_position(self):
        """Reason is deliberately NOT the last field here -- a position-based
        patch would wrongly overwrite Note instead."""
        case = self._stored_case(
            fields=[
                {"name": "Reason", "content": "old", "inline": False},
                {"name": "Note", "content": "n", "inline": False},
            ],
            channel_id=444,
            message_id=555,
        )
        embed = self.cog.case_embed(case)
        message = FakeReasonMessage(embed)
        self.guild.channels[444] = FakeReasonChannel(message)
        ctx = FakeCommandContext(guild=self.guild)

        await self.cog.reason.callback(self.cog, ctx, 1, reason="new text")

        edited = message.edited_embed
        by_name = {f.name: f.value for f in edited.fields}
        self.assertEqual(by_name["Reason:"], "new text")
        self.assertEqual(by_name["Note:"], "n")


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
            fields=reason_field("because"),
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

    def test_fields_render_in_the_order_given(self):
        """Field order is caller-controlled -- nothing assumes reason (or any
        other field) comes last."""
        embed = self._build(
            fields=[
                {"name": "Note", "content": "n", "inline": False},
                {"name": "Reason", "content": "r", "inline": False},
                {"name": "Extra", "content": "e", "inline": True},
            ]
        )
        names = [f.name for f in embed.fields]

        self.assertEqual(names[-3:], ["Note:", "Reason:", "Extra:"])

    def test_field_inline_is_honoured(self):
        embed = self._build(fields=[{"name": "Extra", "content": "e", "inline": True}])

        self.assertTrue(embed.fields[-1].inline)

    def test_no_fields_produces_no_extra_field(self):
        embed = self._build(fields=[])

        self.assertNotIn("Reason:", [f.name for f in embed.fields])

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

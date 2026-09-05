"""Consuming cogs recording real cases through a real ModLog instance.

These drive the cogs' own methods rather than mocking them, which is how the
optional-actor crash in Topics was found.
"""

from __future__ import annotations

import unittest

import discord

import common.modlog_proxy as proxy_module
import modlog.modlog as modlog_module
from common.modlog_proxy import ModLogProxy

from tests.helpers import (
    FakeBot,
    FakeCoreModlog,
    FakeChannel,
    FakeGuild,
    FakeMember,
    install_pil_stub,
    make_modlog_cog,
)

install_pil_stub()

from topics.topics import Topics  # noqa: E402
from verification.verification import Verification  # noqa: E402


class ConsumerTestCase(unittest.IsolatedAsyncioTestCase):
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

        self.modlog = make_modlog_cog(self.bot)
        self.bot.cogs["ModLog"] = self.modlog

    def tearDown(self) -> None:
        proxy_module.core_modlog = self._original_core
        modlog_module.modlog.get_modlog_channel = self._original_get_channel

    @property
    def stored_cases(self) -> dict:
        return self.modlog.config.data.get("cases", {})

    def cases_of_type(self, action_type: str) -> list:
        return [c for c in self.stored_cases.values() if c["action_type"] == action_type]


class TestVerification(ConsumerTestCase):
    OUTCOMES = (
        ("verification_pass", "Solved the captcha."),
        ("verification_failed", "Submitted an incorrect code. 2 attempt(s) remaining."),
        ("verification_expired", "Did not finish in time. No action taken."),
        ("verification_lockout", "Locked out: ran out of attempts."),
    )

    async def asyncSetUp(self) -> None:
        self.cog = object.__new__(Verification)
        self.cog.bot = self.bot
        self.cog.modlog = ModLogProxy(
            self.cog, action_types=Verification.ACTION_TYPES
        )
        await self.cog.modlog.refresh()
        self.member = FakeMember(222, "newcomer", self.guild)

    async def _record_all(self):
        for action_type, reason in self.OUTCOMES:
            await self.cog._record(
                self.member, action_type=action_type, reason=reason
            )

    async def test_every_outcome_becomes_a_case(self):
        await self._record_all()

        kinds = [self.stored_cases[str(n)]["action_type"] for n in range(1, 5)]
        self.assertEqual(kinds, [outcome[0] for outcome in self.OUTCOMES])

    async def test_the_member_is_the_target(self):
        await self._record_all()

        for case in self.stored_cases.values():
            self.assertEqual(case["target_id"], 222)

    async def test_the_bot_is_the_actor(self):
        """Nobody takes these by hand."""
        await self._record_all()

        for case in self.stored_cases.values():
            self.assertEqual(case["actor_id"], self.guild.me.id)

    async def test_outcomes_land_in_the_members_history(self):
        await self._record_all()

        self.assertEqual(
            self.modlog.config.data["user_cases"]["222"], [1, 2, 3, 4]
        )

    async def test_outcomes_are_colour_coded(self):
        self.assertEqual(
            self.modlog.action_type("verification_pass").color, discord.Colour.green()
        )
        self.assertEqual(
            self.modlog.action_type("verification_lockout").color, discord.Colour.red()
        )

    async def test_falls_back_to_core_when_modlog_is_absent(self):
        """Nothing about verification is anonymity-sensitive."""
        self.bot.cogs.pop("ModLog")

        await self.cog._record(
            self.member, action_type="verification_pass", reason="Solved the captcha."
        )

        self.assertEqual(self.core.created, ["verification_pass"])


class TestTopics(ConsumerTestCase):
    async def asyncSetUp(self) -> None:
        self.cog = object.__new__(Topics)
        self.cog.bot = self.bot
        self.cog.modlog = ModLogProxy(
            self.cog, action_types=Topics.ACTION_TYPES, core_fallback=False
        )
        await self.cog.modlog.refresh()
        self.requester = FakeMember(333, "curious", self.guild)

    async def _request(self, note="can we talk about something else"):
        return await self.cog._log_request(
            self.requester,
            channel=FakeChannel(),
            note=note,
            jump_url="https://discord.com/x/y/z",
        )

    async def test_request_is_recorded(self):
        self.assertTrue(await self._request())
        self.assertEqual(len(self.cases_of_type("topic_change")), 1)

    async def test_requester_is_the_actor(self):
        """Anonymous in the channel, attributed in the modlog. Filing a
        request is something the requester did, not something that happened
        to them, so they are its actor rather than its target."""
        await self._request()

        self.assertEqual(self.cases_of_type("topic_change")[0]["actor_id"], 333)

    async def test_the_case_has_no_target(self):
        """Actor and target being the same person would be redundant --
        there is no one else the request happened to."""
        await self._request()

        self.assertIsNone(self.cases_of_type("topic_change")[0]["target_id"])

    async def test_context_is_preserved_in_the_reason(self):
        await self._request()
        reason = self.cases_of_type("topic_change")[0]["reason"]

        self.assertIn("can we talk about something else", reason)
        self.assertIn("<#77>", reason)
        self.assertIn("https://discord.com/x/y/z", reason)

    async def test_request_lands_in_the_requesters_actions_not_their_cases(self):
        await self._request()

        self.assertIn(1, self.modlog.config.data["actor_cases"]["333"])
        self.assertNotIn("user_cases", self.modlog.config.data)

    async def test_a_noteless_request_still_records(self):
        self.assertTrue(await self._request(note=None))

    async def test_refuses_core_rather_than_deanonymising(self):
        """Core's case lookups are readable by any member."""
        self.bot.cogs.pop("ModLog")

        self.assertFalse(await self._request())
        self.assertEqual(self.core.created, [])

    async def test_no_core_casetype_is_ever_registered(self):
        self.assertNotIn("topic_change", self.core.registered)


if __name__ == "__main__":
    unittest.main()

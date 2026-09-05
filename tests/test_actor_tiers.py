"""resolve_actor_display: the owner/admin/mod/member/bot/user tier ladder."""

from __future__ import annotations

import unittest

from common.actor_tiers import resolve_actor_display

from tests.helpers import FakeBot, FakeGuild, FakeMember, FakeUser


class ActorTiersTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = FakeBot()
        self.guild = FakeGuild(owner_id=100)

    async def resolve(self, actor, *, configured_label=None, configured_emoji=None):
        return await resolve_actor_display(
            self.bot, actor, configured_label=configured_label, configured_emoji=configured_emoji
        )


class _ExplodingBot(FakeBot):
    """A bot whose privilege checks blow up if ever called."""

    async def is_owner(self, user) -> bool:
        raise AssertionError("is_owner should never be called when a label is configured")

    async def is_admin(self, user) -> bool:
        raise AssertionError("is_admin should never be called when a label is configured")

    async def is_mod(self, user) -> bool:
        raise AssertionError("is_mod should never be called when a label is configured")


class TestConfiguredOverride(ActorTiersTestCase):
    async def test_configured_label_short_circuits_everything(self):
        """A configured label returns immediately -- proven by handing it an
        actor whose bot would raise if any privilege check were attempted."""
        self.bot = _ExplodingBot()
        owner = FakeMember(100, "owner", self.guild)

        label, emoji = await self.resolve(owner, configured_label="Requester", configured_emoji="🙋")

        self.assertEqual((label, emoji), ("Requester", "🙋"))


class TestTierLadder(ActorTiersTestCase):
    async def test_bot_is_checked_before_anything_else(self):
        """Even the guild owner's own bot account reads as Bot, not Owner --
        an automated action should never look like a human's."""
        bot_member = FakeMember(100, "ViviBot", self.guild, bot=True)

        label, emoji = await self.resolve(bot_member)

        self.assertEqual((label, emoji), ("Bot", "🤖"))

    async def test_guild_owner(self):
        owner = FakeMember(100, "owner", self.guild)

        label, emoji = await self.resolve(owner)

        self.assertEqual((label, emoji), ("Owner", "👑"))

    async def test_admin(self):
        admin = FakeMember(200, "admin", self.guild)
        self.bot.admin_ids.add(200)

        label, emoji = await self.resolve(admin)

        self.assertEqual((label, emoji), ("Admin", "⚔️"))

    async def test_moderator(self):
        mod = FakeMember(300, "mod", self.guild)
        self.bot.mod_ids.add(300)

        label, emoji = await self.resolve(mod)

        self.assertEqual((label, emoji), ("Moderator", "🛡️"))

    async def test_bot_owner_counts_as_admin_and_moderator(self):
        """is_admin_or_superior/is_mod_or_superior both also grant privilege
        to Red's own bot owner -- unrelated to the guild owner check."""
        owner = FakeMember(400, "botowner", self.guild)
        self.bot.owner_ids.add(400)

        label, emoji = await self.resolve(owner)

        self.assertEqual((label, emoji), ("Admin", "⚔️"))

    async def test_regular_member(self):
        member = FakeMember(500, "regular", self.guild)

        label, emoji = await self.resolve(member)

        self.assertEqual((label, emoji), ("Member", "👤"))

    async def test_former_member_falls_back_to_user(self):
        """A discord.User (left the guild) has no .guild to check a role
        against, so it never reaches the Red API calls at all."""
        former_member = FakeUser(600, "gone")

        label, emoji = await self.resolve(former_member)

        self.assertEqual((label, emoji), ("User", "👤"))

    async def test_unresolvable_id_falls_back_to_user(self):
        label, emoji = await self.resolve(700)

        self.assertEqual((label, emoji), ("User", "👤"))

    async def test_none_actor_falls_back_to_user_without_erroring(self):
        """Callers (ModLog.create_case, log_event) special-case actor=None
        themselves to skip the lookup entirely, since there is no meaningful
        tier for "nobody" -- but this still resolves harmlessly rather than
        raising, since build_case_embed never renders the field anyway when
        actor is None."""
        label, emoji = await self.resolve(None)

        self.assertEqual((label, emoji), ("User", "👤"))


if __name__ == "__main__":
    unittest.main()

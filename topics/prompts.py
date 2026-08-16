"""The built-in conversation starters, and the logic for picking one.

Kept free of any discord.py or Red imports so topic selection can be exercised
from a plain script without standing up a bot.
"""

from __future__ import annotations

import random
from typing import Optional, Sequence

# Deliberately broad and apolitical: these land in whichever channel a guild
# points the cog at, so nothing here should need moderating on its own.
TOPICS = (
    "What's a small thing that reliably improves your day?",
    "What's the best meal you've had in the last month?",
    "What hobby would you take up if money and time weren't a factor?",
    "What's a skill you picked up faster than you expected?",
    "What's the last thing that made you laugh out loud?",
    "If you could instantly master one instrument, which would it be?",
    "What's a film you'd happily watch again tonight?",
    "What's the most useful thing you own that cost under twenty bucks?",
    "What's a place you'd go back to in a heartbeat?",
    "What's something you believed as a kid that turned out to be nonsense?",
    "What's the best advice you've ever ignored?",
    "What's your go-to comfort food?",
    "What's a song you can't skip when it comes on?",
    "What's the strangest job you've ever had or heard of?",
    "What fictional world would you least want to live in?",
    "What's something you're weirdly good at?",
    "What's the last thing you learned that surprised you?",
    "Early morning or late night — which are you, and why?",
    "What's a book, show, or game you'd recommend to anyone?",
    "What's the best purchase you made this year?",
    "What's a small act of kindness you still remember receiving?",
    "If you had a completely free weekend, what would you do with it?",
    "What's a food combination you love that other people find odd?",
    "What's the most beautiful place you've been to in person?",
    "What's something that's massively overrated?",
    "What's something that deserves way more attention than it gets?",
    "What's your favourite way to waste an hour?",
    "What's the first concert or live event you went to?",
    "What's a piece of technology you genuinely miss?",
    "What's a habit you've built that actually stuck?",
    "What's the best gift you've ever given someone?",
    "If you could have dinner with any three people, living or dead, who?",
    "What's something you've changed your mind about recently?",
    "What's the most useless fact you know?",
    "What's a smell that instantly takes you somewhere else?",
    "What would the title of your autobiography be?",
    "What's the pettiest hill you're willing to die on?",
    "What's your ideal way to spend a rainy afternoon?",
    "What's a tradition from your family or culture you'd like to keep going?",
    "What's the best thing you've ever cooked?",
    "What's something you're looking forward to?",
    "If you had to teach a class on anything, what would it be?",
    "What's a small luxury you refuse to give up?",
    "What's a piece of media that shaped how you see the world?",
    "What animal would you most like to be for a day?",
    "What's the worst haircut you've ever had?",
    "What's a word or phrase you use far too often?",
    "What's the longest you've stayed up, and what for?",
    "What's a video game or board game you'll never get tired of?",
    "What's your most-used app that isn't a social one?",
    "What's the best thing about where you live right now?",
    "What's something you'd like to be better at a year from now?",
    "If you could un-see one thing so you could enjoy it fresh, what?",
    "What's the most spontaneous thing you've ever done?",
    "What's a rule you follow that nobody asked you to?",
    "What sound do you find weirdly satisfying?",
    "What's your favourite season, and what makes it?",
    "What's something you own that has a good story behind it?",
    "What's a compliment you've received that stuck with you?",
    "What's the best way to spend a birthday, in your opinion?",
)


def pick(pool: Sequence[str], recent: Sequence[str]) -> Optional[str]:
    """Return a topic from ``pool``, avoiding ``recent`` where possible.

    A guild with only a handful of custom topics would otherwise run dry the
    moment ``recent`` covered the whole pool, so exhausting the choices falls
    back to the full pool rather than returning nothing.
    """
    if not pool:
        return None
    seen = set(recent)
    unseen = [topic for topic in pool if topic not in seen]
    return random.choice(unseen or list(pool))

"""Migrated, and last in discovery order but first in the chain."""
from jbcub_bot.core.pipeline import LOOKUP


async def find_name(message):
    return None


def register(bot) -> None:
    bot.describe("🔎", "Zulu", "Last in discovery order.")
    bot.message(at=LOOKUP, description="Zulu looks a name up.")(find_name)

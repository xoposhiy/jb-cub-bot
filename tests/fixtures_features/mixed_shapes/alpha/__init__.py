"""Migrated: `register(bot)` and nothing else."""
from jbcub_bot.core.pipeline import AGENT


async def say_alpha(message):
    return None


async def read_anything(message):
    return None


def register(bot) -> None:
    bot.describe("🔤", "Alpha", "First in discovery order.")
    bot.command("alpha", "Do the alpha thing.")(say_alpha)
    bot.message(at=AGENT, description="Alpha reads what is left.")(read_anything)

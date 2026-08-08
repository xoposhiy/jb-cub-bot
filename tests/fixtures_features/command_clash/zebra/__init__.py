"""Last in discovery order, and claims /ping too."""


async def ping(message):
    return None


def register(bot) -> None:
    bot.describe("🦓", "Zebra", "Claims /ping as well.")
    bot.command("ping", "Ping the zebra.")(ping)

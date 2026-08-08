"""First in discovery order, and claims /ping."""


async def ping(message):
    return None


def register(bot) -> None:
    bot.describe("🐜", "Aardvark", "Claims /ping first.")
    bot.command("ping", "Ping the aardvark.")(ping)

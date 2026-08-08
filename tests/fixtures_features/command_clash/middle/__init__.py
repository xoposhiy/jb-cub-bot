"""Between the two clashing features, and itself blameless."""


async def middle(message):
    return None


def register(bot) -> None:
    bot.describe("🙂", "Middle", "Claims nothing anyone else wants.")
    bot.command("middle", "Do the middle thing.")(middle)

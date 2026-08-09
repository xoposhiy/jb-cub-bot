"""The knowledge base: one chain slot, /ask, /kb_reload and a rating pair.

The chain slot sits at `AGENT`, behind `directory`'s name search at `LOOKUP`,
and that `at=` is the whole of the routing between the two: a name shows a
person, anything else becomes a question. See `handlers.py` for why there is
nothing else -- no state, no offer button, no mode to leave.
"""
from jbcub_bot.core.contract import TEXT
from jbcub_bot.core.models import Role
from jbcub_bot.core.pipeline import AGENT
from jbcub_bot.features.kb import handlers


def register(bot) -> None:
    bot.describe("📚", "Kb", "Ask the program's knowledge base a question.")

    bot.command("ask", "Ask the knowledge base a question.",
                usage="[question]")(handlers.cmd_ask)
    bot.command("kb_reload", "Re-download the knowledge base now.",
                role=Role.ADMIN)(handlers.cmd_kb_reload)

    bot.message(at=AGENT, when=TEXT,
                description="ask the knowledge base a question",
                )(handlers.answer_question)

    bot.button(handlers.RATE_CALLBACK)(handlers.cb_rate)

    # How this feature learns that something else answered the last message in
    # a chat -- a profile, say -- without knowing that `directory` exists.
    handlers.set_registry(bot.registry)

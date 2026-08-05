from jbcub_bot.core.loader import Manifest
from jbcub_bot.features.kb.handlers import cmd, kb_offer_intent, router

manifest = Manifest(
    name="kb",
    commands=cmd.specs,
    intents=[kb_offer_intent],
    help_text="Ask the program's knowledge base a question.",
    emoji="📚",
)

__all__ = ["router", "manifest"]

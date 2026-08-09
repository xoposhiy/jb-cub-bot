"""A tap on an inline button, and which handler it belongs to.

`callback_data` is a key exactly one handler wants, so unlike the message chain
there is no order to settle -- only how much of the data is the key and how
much is the payload. The longest registered key that prefixes the data wins,
which is what lets `dir:admin` and `dir:admin:page` coexist.

Telegram keeps the button spinning until the callback is answered, so a tap
nothing matched -- a keyboard left over from an older deploy, usually -- is
answered here rather than left hanging.
"""
from aiogram.types import CallbackQuery

from jbcub_bot.core.contract import ButtonSpec, Registry, call_handler
from jbcub_bot.core.guards import refusal


def match(buttons: list[ButtonSpec], data: str) -> tuple[ButtonSpec, str] | None:
    """The longest registered key that prefixes `data`, and the rest as `arg`.

    Pure, so the prefix question is testable without a bot. A key is written
    without its trailing separator (`bot.button("dir:link")`), so the ":" that
    follows belongs to neither side and is dropped.
    """
    candidates = [spec for spec in buttons if data.startswith(spec.key)]
    if not candidates:
        return None
    spec = max(candidates, key=lambda spec: len(spec.key))
    arg = data[len(spec.key):]
    return spec, arg[1:] if arg.startswith(":") else arg


async def handle_callback(registry: Registry, callback: CallbackQuery, *,
                          principal, session, bot, impersonator, dialog,
                          oplog) -> None:
    """Route one tap, and answer it even when no key matched."""
    given = dict(principal=principal, session=session, bot=bot,
                 impersonator=impersonator, dialog=dialog, oplog=oplog)
    if not await take_callback(registry, callback, **given):
        await callback.answer()


async def take_callback(registry: Registry, callback: CallbackQuery, *,
                        principal, session, bot, impersonator, dialog,
                        oplog) -> bool:
    """True when the tap was taken or refused -- either way it was answered.

    False means no registered key matched *and* nothing was said, which the
    caller above turns into a bare `answer()`. Split out for the same reason as
    `pipeline.take_message`.
    """
    found = match(registry.buttons(), callback.data or "")
    if found is None:
        return False
    spec, arg = found
    refused = refusal(spec.guard, principal)
    if refused is not None:
        # An alert, not a toast: the screen did not change, and a toast on an
        # unchanged screen is easy to miss.
        await callback.answer(refused, show_alert=True)
        return True
    await call_handler(spec.handler, callback, principal=principal,
                       session=session, bot=bot, impersonator=impersonator,
                       dialog=dialog, arg=arg, oplog=oplog)
    return True

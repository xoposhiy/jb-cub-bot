"""A tap on an inline button, and which handler it belongs to.

`callback_data` is a key exactly one handler wants, so unlike the message chain
there is no order to settle here -- only how much of the data is the key and how
much is the payload. The longest registered key that prefixes the data wins,
which is what lets `dir:admin` and `dir:admin:page` coexist and replaces the
hand-written `cb.data[len(PREFIX):]` slicing in every feature.

Telegram keeps the button spinning until the callback is answered, so a tap
nothing matched -- a keyboard left over from an older deploy, usually -- is
answered here rather than left hanging.
"""
from aiogram.types import CallbackQuery

from jbcub_bot.core.contract import ButtonSpec, Registry, call_handler
from jbcub_bot.core.guards import refusal


def match(buttons: list[ButtonSpec], data: str) -> tuple[ButtonSpec, str] | None:
    """The longest registered key that prefixes `data`, and the rest as `arg`.

    Pure, so the whole prefix question is testable without a bot. A key is
    written without its trailing separator (`bot.button("dir:link")`), so the
    ":" that follows it belongs to neither side and is dropped: the handler
    asked for the matriculation, not for ":30000001".
    """
    candidates = [spec for spec in buttons if data.startswith(spec.key)]
    if not candidates:
        return None
    spec = max(candidates, key=lambda spec: len(spec.key))
    arg = data[len(spec.key):]
    return spec, arg[1:] if arg.startswith(":") else arg


async def handle_callback(registry: Registry, callback: CallbackQuery, *,
                          principal, session, bot, impersonator, dialog,
                          oplog) -> ButtonSpec | None:
    """Route one tap. Returns the spec that took it, or None.

    Every injectable is passed by name, including the ones that are nobody --
    `impersonator=None` outside /as, `principal=None` for an unlinked caller.
    `call_handler` injects a name only when the core offered it, so supplying
    all seven here is what keeps "declared ⇒ injected" true without exception.
    """
    found = match(registry.buttons(), callback.data or "")
    if found is None:
        await callback.answer()
        return None
    spec, arg = found
    refused = refusal(spec.guard, principal)
    if refused is not None:
        # An alert rather than a toast: the tap did nothing, and a toast on a
        # screen that did not change is easy to miss.
        await callback.answer(refused, show_alert=True)
        return None
    await call_handler(spec.handler, callback, principal=principal,
                       session=session, bot=bot, impersonator=impersonator,
                       dialog=dialog, arg=arg, oplog=oplog)
    return spec

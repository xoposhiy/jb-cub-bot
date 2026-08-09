"""One user's open dialog, and who it belongs to.

The core owns dialog identity: the state name is derived from the feature and
the dialog's own name, so a feature writes no `StatesGroup` and cannot collide
with another. `owner()` answers *whose* dialog is open rather than whether one
is, which is what lets a message reach the dialog waiting for it while the
chain still runs for everybody else -- the thing a global `StateFilter(None)`
cannot do.

No step graph and no timeout: a multi-step dialog keeps its step in its own
data. The FSM is in memory, so a dialog dies on redeploy -- accepted, and not
worth a persistence layer.
"""
from dataclasses import dataclass

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext


def state_name(feature: str, dialog: str) -> str:
    """The one definition of a dialog's identity: `Registry.dialogs()` keys on
    it, `DialogHandle` opens it, and `owner()` returns it."""
    return f"{feature}:{dialog}"


class Dialog:
    """Façade over one user's `FSMContext`. Injected as `dialog`.

    A feature reaches its own dialog through the `DialogHandle` that
    `bot.dialog(...)` handed back, so nothing here knows who is calling.
    """

    def __init__(self, state: FSMContext):
        self._state = state

    async def owner(self) -> str | None:
        """The state name of the open dialog, or None when there is none."""
        return await self._state.get_state()

    async def start(self, name: str, /, **data) -> None:
        """Open `name` (a state name -- `DialogHandle.start` is how to get one)
        with `data` as its whole contents.

        Whatever was open is replaced: one FSM per user, and carrying the
        previous dialog's data over would hand a feature values it never
        stored. `name` is positional-only so a dialog may use that key itself.
        """
        await self._state.set_state(name)
        await self._state.set_data(data)

    async def data(self) -> dict:
        return await self._state.get_data()

    async def update(self, **data) -> None:
        """Merge into what the dialog is holding."""
        await self._state.update_data(**data)

    async def end(self) -> None:
        """Close the dialog and forget its data -- leaving the data is how a
        dialog reopens holding somebody's old answer."""
        await self._state.clear()


@dataclass(frozen=True)
class DialogHandle:
    """What `bot.dialog(...)` hands back: a feature's way to open its own
    dialog without writing the state name out and getting it wrong."""
    feature: str
    name: str

    @property
    def state(self) -> str:
        return state_name(self.feature, self.name)

    async def start(self, dialog: Dialog, **data) -> None:
        await dialog.start(self.state, **data)


class DialogMiddleware(BaseMiddleware):
    """Puts a `Dialog` in `data["dialog"]` for messages and callbacks.

    It wraps the `FSMContext` aiogram already resolved into `data["state"]`,
    keyed per user-in-chat, rather than building storage of its own -- that is
    what keeps a tap and the message after it looking at the same dialog. A
    missing key means this was mounted where aiogram resolves no FSM context,
    and the KeyError says so.
    """

    async def __call__(self, handler, event, data):
        data["dialog"] = Dialog(data["state"])
        return await handler(event, data)

"""One user's open dialog, and who it belongs to.

The core owns dialog identity: a state name is derived from the feature and the
dialog's own name, so a feature writes no `StatesGroup` and cannot collide with
another feature that also calls its dialog "edit". More importantly, `owner()`
answers *whose* dialog is open rather than whether one is -- which is what lets
the pipeline hand a message to the dialog waiting for it while still running
the chain for everybody else. Asking "is anyone in a state?" is what a global
`StateFilter(None)` does, and it lets any one feature silence all the others.

There is no step graph and no timeout here: the whole bot has one dialog with
one state, and a multi-step dialog keeps its step in its own data. The FSM is
in memory, in one process, so a dialog dies on redeploy -- accepted, and not
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

    Deliberately five methods and no more. A feature reaches its own dialog
    through the `DialogHandle` that `bot.dialog(...)` handed back, so nothing
    here needs to know which feature is calling.
    """

    def __init__(self, state: FSMContext):
        self._state = state

    async def owner(self) -> str | None:
        """The state name of the open dialog, or None when there is none."""
        return await self._state.get_state()

    async def start(self, name: str, /, **data) -> None:
        """Open `name` -- a state name, which `DialogHandle.start` is the way
        to get -- with `data` as its whole contents.

        Whatever was open is replaced: one FSM per user means there is no
        second slot, and carrying the previous dialog's data over would hand a
        feature values it never stored. `name` is positional-only so a dialog
        may store a value under that key.
        """
        await self._state.set_state(name)
        await self._state.set_data(data)

    async def data(self) -> dict:
        return await self._state.get_data()

    async def update(self, **data) -> None:
        """Merge into what the dialog is holding."""
        await self._state.update_data(**data)

    async def end(self) -> None:
        """Close the dialog and forget its data -- both, because a state left
        behind with stale data is how a dialog reopens holding somebody's old
        answer."""
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

    aiogram has already resolved the caller's `FSMContext` into `data["state"]`
    by the time an inner middleware runs, keyed per user-in-chat; wrapping that
    rather than building storage here is what keeps a button tap and the
    message that follows it looking at the same dialog. A missing key would
    mean this was mounted somewhere aiogram resolves no FSM context at all, and
    the KeyError says so loudly.
    """

    async def __call__(self, handler, event, data):
        data["dialog"] = Dialog(data["state"])
        return await handler(event, data)

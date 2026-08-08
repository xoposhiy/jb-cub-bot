"""Every incoming message, offered around in one fixed order until something
takes it.

The order is the core's and no feature can change it: a command, then the
sender's own dialog, then the chain in declared `at` order, then the last word.
That is what makes "a command works inside a dialog" a property rather than a
habit -- `~F.text.startswith("/")` has nowhere left to be needed -- and what
keeps one feature's open dialog from silencing every other feature, which is
exactly what a global `StateFilter(None)` does.

The chain contract survives from `core/intents.py` verbatim: `False` means "not
mine" and obliges the handler to have answered nothing, since the next handler
-- or the last word -- is about to answer instead. Anything else, including
`None`, counts as taken, so a handler that forgets to return cannot go silently
unhandled. What changed is that the walk is ordered by the declared position
rather than by registration, the match test is a predicate rather than a regex,
and the taker comes back rather than a bool.

One thing the order does not do: only *text* reaches a dialog's `on_text`. A
photo or a document sent while the sender's own dialog is open skips the dialog
and goes to the chain -- and, with nothing there for it, to "I only read text."
A dialog that wants an upload needs a rule here first; today none does, and the
hook is named for what it takes.
"""
from aiogram.types import Message

from jbcub_bot.core.contract import (
    CANCEL_COMMAND,
    INJECTABLES,
    TEXT,
    MessageSpec,
    Registry,
    call_handler,
)
from jbcub_bot.core.guards import refusal
from jbcub_bot.core.oplog import format_miss

# Landmarks, so a feature declares where it sits by name and the resolved order
# is readable without opening five files. Integers rather than an enum: order
# within a stage would fall back to registration order, and that is the bug
# this replaces.
LOOKUP = 100   # a name finds a classmate
AGENT = 200    # the knowledge-base agent answers
LEGACY = 900   # until phase F: where the legacy-intent shim sits. Dies with it.

NOTHING_MATCHED = "No one found."
NOTHING_TO_CANCEL = "Nothing to cancel."
# What the core says when the dialog declared no on_cancel of its own: it knows
# a dialog ended, not what was on the screen behind it.
CANCELLED = "Cancelled."


def command_of(message: Message) -> tuple[str, str] | None:
    """The command a message addresses and its tail, or None for a non-command.

    One reading, in one place, because two would be a silent routing hole:
    `core/legacy.py`'s filter returning True is a promise that `_run_command`
    will find the same name, and a divergence between them would take the
    update off the legacy router that owned it only to answer "I don't know
    /x." The pair comes back together so neither caller parses twice.
    """
    # aiogram's Command filter matches text *or* caption, so a photo posted
    # with "/sync 2024" as its caption is just as deliberate an address as
    # typing the command -- see the same reading in `core/principal.py`.
    text = message.text or message.caption or ""
    if not text.startswith("/"):
        return None
    # Split on any whitespace, the way aiogram's own Command filter does, so a
    # command pasted with a trailing newline is still that command. `text`
    # starts with "/", so there is always a first word.
    head, *rest = text.split(maxsplit=1)
    # "/me@jbcub_bot" is the same command: Telegram appends the bot's name to
    # every command tapped in a group.
    return head[1:].split("@")[0], rest[0].strip() if rest else ""


async def handle_message(registry: Registry, message: Message, *, principal,
                         session, bot, impersonator, dialog, oplog) -> None:
    """The whole order for one message: a command, the sender's own dialog, the
    chain, and then the last word when none of them took it."""
    given = dict(principal=principal, session=session, bot=bot,
                 impersonator=impersonator, dialog=dialog, oplog=oplog)
    if not await take_message(registry, message, **given):
        await last_word(message, principal=principal,
                        impersonator=impersonator, oplog=oplog)


async def take_message(registry: Registry, message: Message, *, principal,
                       session, bot, impersonator, dialog, oplog) -> bool:
    """Steps 1 to 3. True when something took the message.

    Split from the last word so a caller can offer the message somewhere else
    before anything answers -- which is what the legacy routers need while they
    still exist.
    """
    given = dict(principal=principal, session=session, bot=bot,
                 impersonator=impersonator, dialog=dialog, oplog=oplog)
    # The record describes *this* message from here on. A command or a dialog
    # taking it therefore reads as None: /me shows a profile too, and nothing
    # downstream may mistake that for the chain having answered. A handler that
    # crashes mid-walk leaves None as well, which is the truth -- nothing took
    # the message.
    registry.record_taker(message.chat.id, None)
    command = command_of(message)
    if command is not None:
        # An unknown command falls to the last word rather than into the chain:
        # the sender addressed the bot, not the room.
        return await _run_command(registry, message, *command, **given)
    if TEXT(message) and await _run_dialog(registry, message, **given):
        return True
    return await dispatch(registry, message, **given) is not None


async def dispatch(registry: Registry, message: Message,
                   **injectables) -> MessageSpec | None:
    """Walk the chain in `at` order. Return the spec that took the message.

    A handler is offered the message when its predicate passes and its guard
    allows the sender. A guard filters here rather than refusing out loud:
    nothing was addressed to this handler, so "Admins only." would answer a
    question nobody asked.

    No `try` around the walk. The crashed handler may already have answered,
    and trying the next one would answer twice, so the exception aborts the
    chain and reaches aiogram's `dp.errors`.
    """
    # All seven names, always: `call_handler` injects a declared parameter only
    # when the core offers it, so a name missing here would silently keep its
    # default instead of failing.
    given = {name: injectables.get(name) for name in INJECTABLES}
    given["arg"] = ""  # a chain handler is given the message, not a tail of it
    taker = None
    for spec in registry.chain():
        if not spec.when(message):
            continue
        if refusal(spec.guard, given["principal"]) is not None:
            continue
        if await call_handler(spec.handler, message, **given) is not False:
            taker = spec
            break
    registry.record_taker(message.chat.id, taker)
    return taker


async def last_word(message: Message, *, principal, impersonator,
                    oplog) -> None:
    """Nothing took it, so the core answers. A message with no answer at all
    looks to the sender exactly like a bot that is down."""
    # Caption as well as text, so an unknown command sent under a photo is
    # answered as the command it is rather than as an unreadable photo.
    words = (message.text or message.caption or "").split()
    command = words[0] if words else ""
    if command.startswith("/"):
        # The bot answered correctly, so this is not a gap worth logging.
        await message.answer(
            f"I don't know {command}. /help lists what I can do."
        )
        return
    if message.text is not None:
        query, answer = message.text, NOTHING_MATCHED
    else:
        # `.value`, not the enum: aiogram's ContentType is a (str, Enum), so
        # interpolating it would write "ContentType.PHOTO" into the entry.
        query = message.content_type.value
        answer = "I only read text. /help lists what I can do."
    await message.answer(answer)
    await oplog.send(format_miss(
        query=query, answer=answer, principal=principal,
        tg_user=message.from_user, impersonator=impersonator,
    ))


async def _run_command(registry: Registry, message: Message, name: str,
                       arg: str, **given) -> bool:
    """Step 1. False leaves an unknown command to the last word.

    Takes the name and the tail `command_of` already parsed rather than the
    raw text, so there is nowhere left for a second reading to appear.
    """
    if name == CANCEL_COMMAND:
        await _cancel(registry, message, **given)
        return True
    spec = registry.commands().get(name)
    if spec is None:
        return False
    refused = refusal(spec.guard, given["principal"])
    if refused is not None:
        await message.answer(refused)
        return True
    await call_handler(spec.handler, message, **given, arg=arg)
    return True


async def _cancel(registry: Registry, message: Message, **given) -> None:
    """The core's one exit from any dialog, which is why no feature owns a
    `/cancel` of its own and the collision that pushed `kb` off it is gone."""
    dialog = given["dialog"]
    owner = await dialog.owner()
    if owner is None:
        await message.answer(NOTHING_TO_CANCEL)
        return
    spec = registry.dialogs().get(owner)
    # Ended whatever the state was, and whatever the hook does: a state no
    # feature claims any more -- one left behind by an older deploy -- is
    # exactly the one a sender cannot get out of by themselves, and a hook that
    # raises must not strand them in it either. Hence the `finally`.
    #
    # The hook runs *before* the end, though, because it is the dialog's last
    # act and the dialog's data is what it has to work with: `directory`'s
    # redraws the screen the prompt is on, and the chat and message ids of that
    # screen are stored nowhere else. The cost of that order, for whoever writes
    # the second `on_cancel`: a hook that *starts* a dialog has it ended again
    # the moment it returns. No feature does; one that wants to would have to
    # ask for a rule here first.
    #
    # The dialog's guard decides whether the *hook* runs, and nothing else. Not
    # whether the exit runs -- refusing that would lock the sender inside a
    # dialog they cannot leave, which is the opposite of why the core took
    # `/cancel` off `directory`. What a guard covers is the hook's right to
    # assume a principal: an in-memory dialog outlives the row behind it, so an
    # admin resetting somebody's binding mid-`/edit` would otherwise turn that
    # person's next `/cancel` into a redraw of `render_edit(None, ...)`.
    allowed = spec is not None and refusal(spec.guard, given["principal"]) is None
    try:
        if allowed and spec.on_cancel is not None:
            await call_handler(spec.on_cancel, message, **given, arg="")
            return
        await message.answer(CANCELLED)
    finally:
        await dialog.end()


async def _run_dialog(registry: Registry, message: Message, **given) -> bool:
    """Step 2. The sender's own dialog, if the registry routes one.

    An open state the registry knows nothing about -- a legacy feature's own
    FSM, or one left behind by an older deploy -- is not allowed to stop the
    chain: that is the whole difference from `StateFilter(None)`.
    """
    owner = await given["dialog"].owner()
    spec = registry.dialogs().get(owner) if owner is not None else None
    if spec is None:
        return False
    refused = refusal(spec.guard, given["principal"])
    if refused is not None:
        await message.answer(refused)
        return True
    await call_handler(spec.on_text, message, **given, arg="")
    return True

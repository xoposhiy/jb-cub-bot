"""Every incoming message, offered around in one fixed order until something
takes it: a command, then the sender's own dialog, then the chain in declared
`at` order, then the last word. The order is the core's and no feature can
change it -- so a command works inside a dialog, and one open dialog cannot
silence every other feature the way a global `StateFilter(None)` does.

What a feature writes against is in AGENTS.md: "A chain handler returns bool"
and "/cancel is the core's".

Only *text* reaches a dialog's `on_text`. An upload sent mid-dialog skips it
and goes to the chain, where nothing wants it. A dialog that needs a file
requires a rule here first.
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
from jbcub_bot.core.guards import NOT_LINKED, refusal
from jbcub_bot.core.oplog import format_miss

# Landmarks, so a feature declares where it sits by name. Integers rather than
# an enum, which would leave the order within one stage to fall back on
# registration order -- that is, on nothing anybody chose.
LOOKUP = 100   # a name finds a classmate
AGENT = 200    # the knowledge-base agent answers

NOTHING_MATCHED = "No one found."
NOTHING_TO_CANCEL = "Nothing to cancel."
# For a dialog with no on_cancel: the core knows a dialog ended, not what was
# on the screen behind it.
CANCELLED = "Cancelled."


def command_of(message: Message) -> tuple[str, str] | None:
    """The command a message addresses and its tail, or None for a non-command.

    The one reading of it. A second one would be a silent routing hole: "is
    this a command" and "which command is it" disagreeing answers an update as
    something nobody typed.
    """
    # A caption counts: "/sync 2024" under a photo is as deliberate an address
    # as typing it, and aiogram's own Command filter reads it that way too.
    text = message.text or message.caption or ""
    if not text.startswith("/"):
        return None
    # Any whitespace, like aiogram, so a trailing newline still parses.
    head, *rest = text.split(maxsplit=1)
    # Telegram appends the bot's name to a command tapped in a group, and
    # "/me@jbcub_bot" is the same command.
    return head[1:].split("@")[0], rest[0].strip() if rest else ""


async def handle_message(registry: Registry, message: Message, *, principal,
                         session, bot, impersonator, dialog, oplog) -> None:
    """The whole order for one message, with the last word when nothing took
    it."""
    given = dict(principal=principal, session=session, bot=bot,
                 impersonator=impersonator, dialog=dialog, oplog=oplog)
    if not await take_message(registry, message, **given):
        await last_word(message, principal=principal,
                        impersonator=impersonator, oplog=oplog)


async def take_message(registry: Registry, message: Message, *, principal,
                       session, bot, impersonator, dialog, oplog) -> bool:
    """Steps 1 to 3. True when something took the message.

    Split from the last word because "did anything take this" and "what do we
    say when nothing did" are two questions, each worth testing on its own.
    """
    given = dict(principal=principal, session=session, bot=bot,
                 impersonator=impersonator, dialog=dialog, oplog=oplog)
    # The taker record is replaced only once this message is settled, and that
    # is load-bearing: until then it still names what took the *previous* one,
    # which is what a chain handler in the middle of the walk reads.
    #
    # A command or a dialog settles it as None -- /me shows a profile too, and
    # nothing downstream may read that as the chain having answered. A crash
    # leaves None as well, which is the truth, hence the `finally`.
    taker = None
    try:
        command = command_of(message)
        if command is not None:
            # An unknown command falls to the last word rather than into the
            # chain: the sender addressed the bot, not the room.
            return await _run_command(registry, message, *command, **given)
        if TEXT(message) and await _run_dialog(registry, message, **given):
            return True
        taker = await dispatch(registry, message, **given)
        return taker is not None
    finally:
        registry.record_taker(message.chat.id, taker)


async def dispatch(registry: Registry, message: Message,
                   **injectables) -> MessageSpec | None:
    """Walk the chain in `at` order. Return the spec that took the message.

    A guard filters here rather than refusing out loud: nothing was addressed
    to this handler, so "Admins only." would answer a question nobody asked.

    No `try` around the walk. A crashed handler may already have answered, and
    the next one would answer twice, so the exception aborts the chain and
    reaches aiogram's `dp.errors`.

    `take_message` owns the taker record, so there is one writer of it.
    """
    # Every injectable by name, always: `call_handler` passes a declared
    # parameter only when the core offers it, so a name missing here would
    # silently keep its default instead of failing.
    given = {name: injectables.get(name) for name in INJECTABLES}
    given["arg"] = ""  # a chain handler is given the message, not a tail of it
    for spec in registry.chain():
        if not spec.when(message):
            continue
        if refusal(spec.guard, given["principal"]) is not None:
            continue
        if await call_handler(spec.handler, message, **given) is not False:
            return spec
    return None


async def last_word(message: Message, *, principal, impersonator,
                    oplog) -> None:
    """Nothing took it, so the core answers -- a message with no answer at all
    looks like a bot that is down.

    Also the one place that tells a stranger they are one, rather than any
    guard: see AGENTS.md, "The chain is for people the bot knows".
    """
    # Caption too, so an unknown command under a photo is answered as the
    # command it is rather than as an unreadable photo.
    words = (message.text or message.caption or "").split()
    command = words[0] if words else ""
    if command.startswith("/"):
        # No ops-log miss: the bot answered correctly, which is not a gap.
        #
        # It points at /help rather than listing what this caller may run.
        # Rendering that list belongs to `features/help`, and the core does not
        # import a feature -- a feature wanting it inline has to offer it.
        await message.answer(
            f"I don't know {command}. /help lists what I can do."
        )
        return
    if principal is None:
        # After the unknown command above, not before: /help is public and is
        # what an unlinked sender should be pointed at. A *known* command never
        # gets here -- its own guard says the same in `_run_command`. No miss
        # logged either: who the bot does not know is not a gap in what it
        # knows.
        await message.answer(NOT_LINKED)
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

    Takes the name and tail `command_of` already parsed, not the raw text, so
    there is nowhere for a second reading to appear.
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
    `/cancel` of its own."""
    dialog = given["dialog"]
    owner = await dialog.owner()
    if owner is None:
        await message.answer(NOTHING_TO_CANCEL)
        return
    spec = registry.dialogs().get(owner)
    # `finally`, so the state ends whatever the hook does: a state no feature
    # claims any more -- one left by an older deploy -- is exactly the one a
    # sender cannot leave by themselves, and a hook that raises must not strand
    # them in it either.
    #
    # The hook runs *before* the end because the dialog's data is what it works
    # with: a hook that redraws a screen finds its message id nowhere else. The
    # cost is that a hook which *starts* a dialog has it ended again on return.
    # A feature that wants to needs a rule here first.
    #
    # The guard decides whether the *hook* runs, and nothing else -- refusing
    # the exit itself would lock the sender in. What it covers is the hook's
    # right to assume a principal: an in-memory dialog outlives the row behind
    # it, so a binding reset mid-dialog would otherwise redraw for nobody.
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

    An open state the registry knows nothing about -- one left by an older
    deploy -- must not stop the chain: that is the difference from
    `StateFilter(None)`.
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

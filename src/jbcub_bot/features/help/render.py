"""What /help says, assembled from what the features declared.

The core collects and this decides what to print: `Registry.features()` hands
over every declaration as data, and every choice made below -- the grouping, the
order of the lines, the badge, where `/cancel` goes -- is this feature's
presentation of it. That seam is why the core has nothing to say about /help
beyond holding the declarations, and it is the reason this module lives here
rather than in `core/`: it had exactly one caller, `register` below, so in the
core it would have had the core's address and this feature's lifetime.

The one thing it must *not* decide is who may see a line. `guards.allowed` does,
and only it -- filtering on `spec.guard.role` here instead would be a second
place deciding visibility, and the contract has one on purpose. A check the
guard cannot express (the bootstrap admin, a profile with no saved row) is
ordinary code inside the feature that owns it and stays listed while it refuses.

Grouped by feature, always: a feature that declared anything this caller may use
gets its heading, and an elevated line stays under that heading with a badge
saying so. That replaces two things the pre-contract renderer got wrong -- a
heading that appeared only if a student-visible line survived, so `impersonate`
never had one, and a pooled trailing `🔐 Admin` block that tore every elevated
line away from the feature it belongs to.
"""
from jbcub_bot.core import guards
from jbcub_bot.core.contract import (
    CANCEL_COMMAND,
    CANCEL_DESCRIPTION,
    FeatureRegistration,
    Guard,
    NoteSpec,
)
from jbcub_bot.core.models import Role, User

UNLINKED_NOTICE = "You're not linked yet — ask a program admin for a one-time link."
CONTACT_NOTICE = "Having trouble with the bot? Message Pavel Egorov."

# The wording matches the refusal the same guard would answer with -- see
# `core/guards.py`, "Admins only." / "Staff only." -- so a line reads the same
# whether you meet it in /help or when it turns you down. Everybody outranks
# STUDENT, so a guard asking for it refuses nobody and earns no badge.
_BADGE = {Role.ADMIN: " (admin)", Role.TEACHER: " (staff)"}


def render_help(features: list[FeatureRegistration],
                principal: User | None) -> str:
    """The whole of /help for one caller."""
    parts = [block for block in (_block(reg, principal) for reg in features)
             if block]
    if principal is None:
        # The one thing that would actually help an unlinked caller, so it is
        # the whole answer when there is nothing else to say.
        parts.append(UNLINKED_NOTICE)
    parts.append(CONTACT_NOTICE)
    return "\n\n".join(parts)


def _block(reg: FeatureRegistration, principal: User | None) -> str:
    """One feature's heading and its visible lines, or "" when this caller may
    use nothing it declared -- a heading over no lines says nothing twice."""
    lines = _lines(reg, principal)
    if not lines:
        return ""
    heading = (f"{reg.description.emoji} {reg.description.title} — "
               f"{reg.description.summary}")
    return "\n".join([heading, *lines])


def _lines(reg: FeatureRegistration, principal: User | None) -> list[str]:
    """Commands first, then the chain, then the notes: the order a reader wants
    is what to type, then what happens without typing a command, then the rest."""
    lines: list[str] = []
    for spec in reg.commands:
        if spec.listed and guards.allowed(spec.guard, principal):
            lines.append(_command_line(spec.name, spec.usage, spec.description,
                                       spec.guard))
    cancel = _cancel_guard(reg, principal)
    if cancel is not None:
        lines.append(_command_line(CANCEL_COMMAND, "", CANCEL_DESCRIPTION,
                                   cancel))
    for spec in reg.messages:
        # No description means the feature documented nothing to list; the
        # handler still runs, it just has no line.
        if spec.description and guards.allowed(spec.guard, principal):
            lines.append(f"  💬 {spec.description}{_badge(spec.guard)}")
    for spec in reg.notes:
        if guards.allowed(spec.guard, principal):
            text = _note_text(spec, principal)
            # A note that resolves to nothing has nothing to say -- kb's lists
            # what is in the base, and an empty base would leave a bare "  ".
            if text:
                lines.append(f"  {text}")
    return lines


def _command_line(name: str, usage: str, description: str, guard: Guard) -> str:
    """`  /name <usage> — description (badge)`; /cancel borrows the same shape."""
    head = f"/{name} {usage}" if usage else f"/{name}"
    return f"  {head} — {description}{_badge(guard)}"


def _cancel_guard(reg: FeatureRegistration,
                  principal: User | None) -> Guard | None:
    """The guard to list `/cancel` under, or None when this feature shows none.

    `/cancel` is the core's, so it belongs to no feature and can have no heading
    of its own in a /help grouped by feature; beside the dialog it ends is the
    truthful place, because that is exactly when it does anything. Once per
    feature, under the first dialog this caller may open: the line is the same
    whichever of them is running.
    """
    for spec in reg.dialogs:
        if guards.allowed(spec.guard, principal):
            return spec.guard
    return None


def _note_text(spec: NoteSpec, principal: User | None) -> str:
    """A note may be a callable of the principal: kb's says what is in the base,
    and that changes on /kb_reload, so a static string would lie."""
    return spec.text(principal) if callable(spec.text) else spec.text


def _badge(guard: Guard) -> str:
    return _BADGE.get(guard.role, "")

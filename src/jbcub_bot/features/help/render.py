from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.middleware import role_rank
from jbcub_bot.core.models import Role, User

_UNLINKED_NOTICE = "You're not linked yet — ask a program admin for a one-time link."


def _command_visible(spec, principal: User | None) -> bool:
    if spec.public:
        return True
    return (principal is not None
            and role_rank(principal.role) >= role_rank(spec.min_role))


def _intent_visible(intent, principal: User | None) -> bool:
    return (principal is not None
            and role_rank(principal.role) >= role_rank(intent.min_role))


def _command_line(spec) -> str:
    head = f"/{spec.name}"
    if spec.usage:
        head += f" {spec.usage}"
    return f"  {head} — {spec.description}"


def _intent_line(intent) -> str:
    return f"  💬 {intent.description}"


def render_help(manifests: list[Manifest], principal: User | None) -> str:
    blocks: list[str] = []
    elevated: list[str] = []  # pooled admin-section lines

    for m in manifests:
        body: list[str] = []
        for spec in m.commands:
            if not _command_visible(spec, principal):
                continue
            if spec.min_role is Role.STUDENT:
                body.append(_command_line(spec))
            else:
                elevated.append(_command_line(spec))
        for intent in m.intents:
            if not _intent_visible(intent, principal):
                continue
            if intent.min_role is Role.STUDENT:
                body.append(_intent_line(intent))
            else:
                elevated.append(_intent_line(intent))
        if body:
            header = f"{m.emoji} {m.name.capitalize()} — {m.help_text}"
            blocks.append("\n".join([header, *body]))

    if principal is None:
        joined = "\n\n".join(blocks)
        return f"{joined}\n\n{_UNLINKED_NOTICE}" if blocks else _UNLINKED_NOTICE

    if elevated:
        blocks.append("\n".join(["🔐 Admin", *elevated]))

    return "\n\n".join(blocks)

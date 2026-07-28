import asyncio
import json
from datetime import date

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from jbcub_bot.core import identity
from jbcub_bot.core.commands import CommandRegistrar
from jbcub_bot.core.config import get_settings
from jbcub_bot.core.intents import Intent
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.tokens import issue_link_token
from jbcub_bot.features.directory.render import (
    ADMIN_BACK_CALLBACK,
    ADMIN_CALLBACK,
    admin_actions_keyboard,
    admin_keyboard,
    admin_row,
    invite_row,
    me_keyboard,
    render_cohort_list,
    render_profile,
)
from jbcub_bot.features.directory import matching
from jbcub_bot.features.directory.search import list_cohort, rank_users

from aiogram.filters import CommandObject

from jbcub_bot.core import sheets
from jbcub_bot.core.sheets_client import build_credentials, fetch_rows
from jbcub_bot.core.tokens import verify_link_token

router = Router(name="directory")
cmd = CommandRegistrar(router)


# A Sheets read that never answers must not take the bot with it. googleapiclient
# already applies a 60s socket timeout, so this only has to be the outer bound.
SHEET_READ_TIMEOUT = 90.0


def set_status(session, user: User, text: str) -> None:
    user.status_line = text
    session.commit()


def is_admin(principal: User | None) -> bool:
    """Whether a departed profile is theirs to see. Named because two readers ask."""
    return principal is not None and principal.role is Role.ADMIN


async def read_rows(sheet_id: str, credentials, range_: str = "A:Z") -> list[list[str]]:
    """`fetch_rows` off the event loop, with a deadline.

    fetch_rows is blocking: awaited inline it stalls every other update until
    Google answers. The thread keeps running after a timeout — it can't be
    cancelled — but the bot stays responsive and the failure gets reported
    instead of looking like a hang.
    """
    async with asyncio.timeout(SHEET_READ_TIMEOUT):
        return await asyncio.to_thread(fetch_rows, sheet_id, credentials, range_)


@cmd.command("me", "Show your own profile.")
async def cmd_me(message: Message, principal: User, session, impersonator=None):
    # `impersonator` is only in the handler context while /as is in flight; a
    # button press afterwards would arrive as the admin, so hide the screen.
    await message.answer(
        render_profile(principal, principal),
        reply_markup=me_keyboard(principal, interactive=impersonator is None),
    )


@cmd.command("cohort", "List the people in your cohort.")
async def cmd_cohort(message: Message, principal: User, session):
    if not principal.primary_cohort:
        await message.answer("No cohort on file.")
        return
    mates = list_cohort(session, principal.primary_cohort,
                        include_departed=is_admin(principal))
    await message.answer("Your cohort:\n" + render_cohort_list(principal, mates))


async def name_search(message: Message, principal: User, session) -> bool:
    """Answer with a profile or a shortlist; return False when unsure.

    Returning False leaves the message unanswered on purpose: the intent
    router moves on, and whatever ends the chain gets to reply.
    """
    if principal is None:
        # Answering here rather than declining: an unlinked user gets told what
        # to do instead of a puzzling "No one found."
        await message.answer("You are not linked yet. Contact an admin.")
        return True
    ranked = rank_users(session, (message.text or "").strip(),
                        include_departed=is_admin(principal))
    if not ranked:
        return False
    best, target = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if best - runner_up >= matching.LEAD:
        kb = admin_keyboard(target) if principal.role is Role.ADMIN else None
        await message.answer(render_profile(principal, target), reply_markup=kb)
        return True
    close = [user for score, user in ranked if best - score <= matching.SPREAD]
    lines = [f"- {user.full_name}" for user in close[:20]]
    await message.answer("Several people match:\n" + "\n".join(lines))
    return True


name_search_intent = Intent(
    name="directory.search",
    pattern=r".+",
    handler=name_search,
    description="just type a name — search people",
)


@router.callback_query(F.data.startswith(f"{ADMIN_CALLBACK}:"))
async def cb_admin_open(cb: CallbackQuery, principal: User, session):
    if principal is None or principal.role is not Role.ADMIN:
        await cb.answer("Admins only.", show_alert=True)
        return
    matriculation = cb.data.split(":", 2)[2]
    # Read the row rather than trusting the profile that was rendered: which
    # actions apply depends on whether the person is linked right now.
    target = identity.find_by_matriculation(session, matriculation)
    if target is None:
        await cb.answer("Not found.", show_alert=True)
        return
    await cb.message.edit_reply_markup(
        reply_markup=admin_actions_keyboard(target))
    await cb.answer()


@router.callback_query(F.data.startswith(f"{ADMIN_BACK_CALLBACK}:"))
async def cb_admin_back(cb: CallbackQuery, principal: User, session):
    if principal is None or principal.role is not Role.ADMIN:
        await cb.answer("Admins only.", show_alert=True)
        return
    matriculation = cb.data.split(":", 2)[2]
    # An admin looking at their own profile came from /me, so put its own
    # buttons back too — not just the collapsed Admin row.
    if principal.matriculation and principal.matriculation == matriculation:
        markup = me_keyboard(principal)
    else:
        markup = InlineKeyboardMarkup(inline_keyboard=[admin_row(matriculation)])
    await cb.message.edit_reply_markup(reply_markup=markup)
    await cb.answer()


@router.callback_query(F.data.startswith("dir:link:"))
async def cb_issue_link(cb: CallbackQuery, principal: User, session):
    if principal is None or principal.role is not Role.ADMIN:
        await cb.answer("Admins only.", show_alert=True)
        return
    matriculation = cb.data.split(":", 2)[2]
    target = identity.find_by_matriculation(session, matriculation)
    if target is None:
        await cb.answer("Not found.", show_alert=True)
        return
    # An invite would bind them a login the middleware refuses on their next
    # message, so say no here rather than let an admin send a dead link.
    if target.departed_at:
        await cb.answer(
            f"{target.full_name} left the roster on {target.departed_at}. "
            f"Put them back in the cohort sheet and re-sync to restore access.",
            show_alert=True,
        )
        return
    # The menu hides this button on a linked profile, but an older message still
    # carries it — and issuing the invite anyway would silently move the profile
    # to whoever taps the link. Reset is where that decision gets confirmed.
    if target.telegram_id is not None:
        await cb.answer(
            f"Already linked to a Telegram account. Reset telegram_id for "
            f"{target.full_name} first.",
            show_alert=True,
        )
        return
    token = issue_link_token(session, matriculation, get_settings().link_secret)
    bot_user = await cb.bot.me()
    ttl = get_settings().link_ttl_seconds
    expires = f"{ttl // 3600}h" if ttl >= 3600 else f"{max(ttl // 60, 1)} min"
    await cb.message.answer(
        f"✉️ Invite for {matriculation}:\n"
        f"https://t.me/{bot_user.username}?start={token}\n\n"
        "Send this to the person. Opening it links whichever Telegram account "
        "taps it to their roster profile, so they can use the bot without "
        "their handle matching the Google Sheet.\n\n"
        f"It works once and expires in {expires}. Anyone who gets the link can "
        "claim the profile — send it in a private chat, and issue a new one if "
        "it leaks."
    )
    await cb.answer()


@router.callback_query(F.data.startswith("dir:reset:"))
async def cb_reset(cb: CallbackQuery, principal: User, session):
    if principal is None or principal.role is not Role.ADMIN:
        await cb.answer("Admins only.", show_alert=True)
        return
    matriculation = cb.data.split(":", 2)[2]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Yes, reset",
                             callback_data=f"dir:reset_do:{matriculation}"),
        InlineKeyboardButton(text="Cancel", callback_data="dir:reset_cancel"),
    ]])
    await cb.message.answer(
        f"🧐 Reset telegram_id for {matriculation}? This unlinks their Telegram "
                f"account — they'll still have access if their telegram handle match the profile "
                f"or need a new one-time link to access the bot.\n\n👿 Use it in case of when user lost access to their Telegram account and created a new one. "
                f"Probably you also need to change their Telegram handle in that case. Change it in the Google Sheet to actual value and run /sync."
                f"\n\n✉️ Issue Invite becomes available once the profile is unlinked.",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("dir:reset_do:"))
async def cb_reset_do(cb: CallbackQuery, principal: User, session):
    if principal is None or principal.role is not Role.ADMIN:
        await cb.answer("Admins only.", show_alert=True)
        return
    matriculation = cb.data.split(":", 2)[2]
    ok = identity.reset_binding(session, matriculation)
    if not ok:
        await cb.message.edit_text("Not found.")
        await cb.answer()
        return
    # Issuing the invite is the whole point of the reset, so offer it here
    # rather than making the admin search for the person again.
    await cb.message.edit_text(
        f"Reset done. {matriculation} is unlinked — the invite below is how "
        "they get back in.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[invite_row(matriculation)]),
    )
    await cb.answer()


@router.callback_query(F.data == "dir:reset_cancel")
async def cb_reset_cancel(cb: CallbackQuery, principal: User, session):
    await cb.message.edit_text("Reset cancelled.")
    await cb.answer()


@cmd.command("start", "Start / link your account.", public=True)
async def cmd_start(message: Message, principal: User, session,
                    command: CommandObject):
    settings = get_settings()
    payload = command.args
    if payload:  # one-time link binding
        user = verify_link_token(session, payload, settings.link_secret,
                                 settings.link_ttl_seconds)
        if user is None:
            await message.answer("This link is invalid or expired.")
            return
        # telegram_id is unique: binding an account that already holds another
        # profile dies on the commit, and the person just never gets a reply.
        # Say so instead, and leave the invite unused so it can be re-sent.
        taken = identity.find_by_telegram_id(session, message.from_user.id)
        if taken is not None and taken.id != user.id:
            await message.answer(
                f"This Telegram account is already linked to {taken.full_name}. "
                "The invite hasn't been used — ask an admin to reset that "
                "binding first."
            )
            return
        identity.bind_by_token(session, message.from_user.id,
                               message.from_user.username, user)
        await message.answer(f"Linked as {user.full_name}.")
        return
    if principal is not None:
        await message.answer(f"Welcome back, {principal.full_name}.")
    else:
        await message.answer(
            "I couldn't recognize you. Ask a program admin for a one-time link."
        )


@cmd.command("sync", "Re-sync roster from Google Sheets.", min_role=Role.ADMIN)
async def cmd_sync(message: Message, principal: User, session):
    settings = get_settings()
    try:
        sa = build_credentials(settings.google_service_account_file,
                               settings.google_service_account_json)
    except (ValueError, FileNotFoundError, IsADirectoryError,
             json.JSONDecodeError) as exc:
        await message.answer(f"Sync aborted (credentials): {exc}")
        return

    # --- Parse phase: fetch + normalize everything; write nothing yet. ---
    await message.answer("Sync started. Reading sheets…")
    try:
        index_rows = await read_rows(settings.rights_sheet_id, sa,
                                     f"{settings.cohorts_tab}!A:Z")
        cohorts = sheets.parse_cohort_index(index_rows)
    except sheets.MappingError as exc:
        await message.answer(f"Sync aborted (Cohorts tab): {exc}")
        return
    except Exception as exc:  # network / API / anything unforeseen
        raise RuntimeError("/sync failed reading the Cohorts tab") from exc

    parsed_cohorts = []  # (cohort_name, records)
    for entry in cohorts:
        await message.answer(f"Reading cohort {entry['cohort']}…")
        sheet_id = sheets.extract_sheet_id(entry["link"])
        try:
            rows = await read_rows(sheet_id, sa)
            records = sheets.normalize_rows(rows, entry["mapping"])
        except sheets.MappingError as exc:
            await message.answer(f"Sync aborted (cohort {entry['cohort']}): {exc}")
            return
        except Exception as exc:
            raise RuntimeError(
                f"/sync failed reading cohort {entry['cohort']} (sheet {sheet_id})"
            ) from exc
        # No roster rows at all is never a cohort that emptied out; it is a blank
        # first data row or a link pointing at the wrong sheet. Writing it would
        # mark every one of its students departed and hide the cohort from itself.
        if not records:
            await message.answer(
                f"Sync aborted (cohort {entry['cohort']}): the sheet yielded no "
                "roster rows, which would mark the whole cohort departed. Check "
                "the Link and that the first data row names someone."
            )
            return
        for record in records:
            record["primary_cohort"] = entry["cohort"]
        parsed_cohorts.append((entry["cohort"], records))
        # The roster ends at the first row naming nobody; below it sit students
        # who left. Ignoring them is intended, ignoring them quietly is not.
        ignored = max(0, len(rows) - 1 - len(records))
        tail = f" {ignored} rows below the roster ignored." if ignored else ""
        await message.answer(
            f"Cohort {entry['cohort']}: {len(records)} rows read.{tail}")

    await message.answer("Reading Rights…")
    try:
        rights_rows = await read_rows(settings.rights_sheet_id, sa,
                                      f"{settings.rights_tab}!A:Z")
        # The Rights tab names its columns with our own field names, so it maps
        # to itself. Rows are keyed on the handle, so that column must be there.
        rights_mapping = sheets.identity_mapping(
            rights_rows[0] if rights_rows else [], required=("handle_sheet",)
        )
        rights_records = sheets.normalize_rows(rights_rows, rights_mapping)
    except sheets.MappingError as exc:
        await message.answer(f"Sync aborted (Rights tab): {exc}")
        return
    except Exception as exc:
        raise RuntimeError("/sync failed reading the Rights tab") from exc

    for record in rights_records:
        role_value = record.get("role")
        if role_value:
            try:
                Role(role_value)
            except ValueError:
                await message.answer(
                    f"Sync aborted (Rights tab): invalid role {role_value!r}"
                )
                return
    await message.answer(f"Rights: {len(rights_records)} rows read.")

    # --- Write phase: everything parsed OK, now upsert + reconcile. ---
    await message.answer("All sheets read. Writing to database…")
    try:
        today = date.today().isoformat()
        for cohort_name, records in parsed_cohorts:
            sheets.upsert_users(session, records)
            # After the upsert, so anyone the roster names again is already back
            # before the ones it dropped get marked.
            departed = sheets.mark_departed(session, cohort_name, records, today)
            rep = sheets.reconcile(session, records)
            await message.answer(
                f"{cohort_name}: {len(records)} rows, "
                f"{departed} marked departed, drift={rep.drift or '-'}, "
                f"unmatched={rep.unmatched or '-'}, dup={rep.duplicates or '-'}")
        # Rights rows have no matriculation — key on the Telegram handle so
        # admins/teachers get matched (or created) as searchable rows.
        sheets.upsert_users(session, rights_records, key="handle_sheet")
        rep = sheets.reconcile(session, rights_records, key="handle_sheet")
        await message.answer(
            f"rights: {len(rights_records)} rows, drift={rep.drift or '-'}, "
            f"unmatched={rep.unmatched or '-'}, dup={rep.duplicates or '-'}")
        session.commit()
    except Exception as exc:
        session.rollback()  # the roster keeps its last good state
        raise RuntimeError("/sync failed in the write phase") from exc
    await message.answer("Sync done.")

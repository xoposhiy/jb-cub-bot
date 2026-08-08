"""One public/role decision, and the wording that goes with refusing it."""
from jbcub_bot.core import guards
from jbcub_bot.core.contract import Guard
from jbcub_bot.core.models import Role, User


def _student():
    return User(last_name="S", role=Role.STUDENT)


def _teacher():
    return User(last_name="T", role=Role.TEACHER)


def _admin():
    return User(last_name="A", role=Role.ADMIN)


# --- no guard: a principal is required, any role passes -----------------------

def test_no_guard_refuses_the_unlinked():
    assert guards.refusal(Guard(), None) == guards.NOT_LINKED
    assert guards.allowed(Guard(), None) is False


def test_no_guard_allows_a_student():
    assert guards.refusal(Guard(), _student()) is None
    assert guards.allowed(Guard(), _student()) is True


# --- public=True: no principal needed -----------------------------------------

def test_public_allows_the_unlinked():
    assert guards.refusal(Guard(public=True), None) is None
    assert guards.allowed(Guard(public=True), None) is True


def test_public_allows_a_student():
    assert guards.refusal(Guard(public=True), _student()) is None


# --- role=ADMIN ---------------------------------------------------------------

def test_admin_only_refuses_the_unlinked_for_not_being_linked():
    guard = Guard(role=Role.ADMIN)
    assert guards.refusal(guard, None) == guards.NOT_LINKED


def test_admin_only_refuses_a_student():
    guard = Guard(role=Role.ADMIN)
    assert guards.refusal(guard, _student()) == guards.ADMIN_REFUSAL
    assert guards.allowed(guard, _student()) is False


def test_admin_only_refuses_a_teacher():
    guard = Guard(role=Role.ADMIN)
    assert guards.refusal(guard, _teacher()) == guards.ADMIN_REFUSAL


def test_admin_only_allows_an_admin():
    guard = Guard(role=Role.ADMIN)
    assert guards.refusal(guard, _admin()) is None
    assert guards.allowed(guard, _admin()) is True


# --- role=TEACHER -------------------------------------------------------------

def test_staff_only_refuses_a_student():
    guard = Guard(role=Role.TEACHER)
    assert guards.refusal(guard, _student()) == guards.STAFF_REFUSAL
    assert guards.allowed(guard, _student()) is False


def test_staff_only_allows_a_teacher():
    guard = Guard(role=Role.TEACHER)
    assert guards.refusal(guard, _teacher()) is None
    assert guards.allowed(guard, _teacher()) is True


def test_staff_only_allows_an_admin():
    guard = Guard(role=Role.TEACHER)
    assert guards.refusal(guard, _admin()) is None
    assert guards.allowed(guard, _admin()) is True


def test_staff_only_refuses_the_unlinked_for_not_being_linked():
    assert guards.refusal(Guard(role=Role.TEACHER), None) == guards.NOT_LINKED


# --- the wordings are the ones the bot already uses ---------------------------

def test_the_wordings_are_unchanged():
    assert guards.ADMIN_REFUSAL == "Admins only."
    assert guards.STAFF_REFUSAL == "Staff only."
    assert guards.NOT_LINKED == "You are not linked yet. Contact an admin."

"""Who each admin is currently viewing the bot as."""

from jbcub_bot.core import impersonation


def test_nobody_is_impersonating_by_default():
    assert impersonation.ref_for(777) is None


def test_begin_then_ref_for_returns_the_target():
    impersonation.begin(777, "30000001")
    assert impersonation.ref_for(777) == "30000001"


def test_one_admins_mode_does_not_leak_to_another():
    impersonation.begin(777, "30000001")
    assert impersonation.ref_for(778) is None


def test_end_returns_the_ref_and_clears_it():
    impersonation.begin(777, "30000001")
    assert impersonation.end(777) == "30000001"
    assert impersonation.ref_for(777) is None


def test_end_without_a_mode_is_not_an_error():
    assert impersonation.end(777) is None


def test_reset_clears_every_admin():
    impersonation.begin(777, "30000001")
    impersonation.begin(778, "30000002")
    impersonation.reset()
    assert impersonation.ref_for(777) is None
    assert impersonation.ref_for(778) is None

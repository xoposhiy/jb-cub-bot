from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory import accounts, edit
from jbcub_bot.features.directory.render import PROFILE_CALLBACK
from jbcub_bot.features.directory.visibility import BY_NAME, EDITABLE_FIELDS


def _me(**kw):
    return User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                primary_cohort="2024", **kw)


def test_screen_lists_every_editable_field_with_its_value():
    text = edit.render_edit(_me(status_line="open to teams",
                                github_self="alice"))
    assert "Edit your profile" in text
    assert "Status: open to teams" in text
    assert "GitHub: alice" in text
    assert "Codeforces: —" in text


def test_screen_never_offers_a_field_the_user_does_not_own():
    text = edit.render_edit(_me(gmail="i@gmail.com", birthday="2000-01-02"))
    assert "Gmail" not in text
    assert "Birthday" not in text


def test_screen_shows_the_roster_value_next_to_a_differing_own_one():
    text = edit.render_edit(_me(github_self="alice-dev", github_sheet="alice"))
    assert "GitHub: alice-dev (roster: alice)" in text


def test_screen_carries_a_notice_above_the_header():
    text = edit.render_edit(_me(), notice="✅ GitHub updated.")
    assert text.startswith("✅ GitHub updated.\n\nEdit your profile")


def test_keyboard_puts_two_fields_per_row_and_back_alone():
    kb = edit.edit_keyboard(_me())
    assert [len(row) for row in kb.inline_keyboard] == [2, 1, 1]
    assert kb.inline_keyboard[-1][0].callback_data == PROFILE_CALLBACK
    assert kb.inline_keyboard[-1][0].text == "← Back to profile"


def test_keyboard_buttons_carry_their_field():
    kb = edit.edit_keyboard(_me())
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert f"{edit.FIELD_CALLBACK_PREFIX}github" in data
    assert f"{edit.FIELD_CALLBACK_PREFIX}status_line" in data


def test_every_callback_data_fits_telegram_s_64_byte_limit():
    keyboards = [edit.edit_keyboard(_me()),
                 edit.prompt_keyboard(BY_NAME["codeforces"]),
                 edit.clear_confirm_keyboard(BY_NAME["codeforces"])]
    for kb in keyboards:
        for row in kb.inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode()) <= 64


def test_prompt_asks_for_the_field_and_shows_what_is_being_replaced():
    text = edit.render_prompt(_me(github_self="alice-dev", github_sheet="alice"),
                              BY_NAME["github"])
    assert "Send your GitHub username" in text
    # The prompt replaces the user's own value, so the roster's is not shown
    # here -- it is not what a new value would overwrite.
    assert "Now: alice-dev" in text
    assert "roster" not in text


def test_prompt_shows_a_dash_when_there_is_nothing_to_replace():
    assert "Now: —" in edit.render_prompt(_me(), BY_NAME["codeforces"])


def test_prompt_shows_a_long_status_in_full_so_it_can_be_retyped():
    status = "x" * 100
    assert status in edit.render_prompt(_me(status_line=status),
                                       BY_NAME["status_line"])


def test_prompt_keyboard_offers_clear_and_cancel():
    kb = edit.prompt_keyboard(BY_NAME["github"])
    assert [b.callback_data for b in kb.inline_keyboard[0]] == [
        f"{edit.CLEAR_CALLBACK_PREFIX}github", edit.CANCEL_CALLBACK,
    ]


def test_clear_asks_before_removing_the_value():
    spec = BY_NAME["github"]
    assert "Clear your GitHub?" in edit.render_clear_confirm(spec)
    kb = edit.clear_confirm_keyboard(spec)
    assert [b.callback_data for b in kb.inline_keyboard[0]] == [
        f"{edit.CLEAR_DO_CALLBACK_PREFIX}github", edit.CANCEL_CALLBACK,
    ]
    assert kb.inline_keyboard[0][0].text == "Yes, clear GitHub"


def test_clear_prefix_does_not_match_the_clear_do_payload():
    # Both handlers filter by prefix; one must not swallow the other's taps.
    assert not f"{edit.CLEAR_DO_CALLBACK_PREFIX}github".startswith(
        edit.CLEAR_CALLBACK_PREFIX)


def test_editable_spec_refuses_a_field_the_user_may_not_edit():
    assert edit.editable_spec("github") is BY_NAME["github"]
    assert edit.editable_spec("gmail") is None      # configurable, not editable
    assert edit.editable_spec("birthday") is None   # admin-only
    assert edit.editable_spec("nonsense") is None


def test_every_editable_field_has_a_normalizer():
    # The field table decides what is editable; accounts.py must keep up.
    for spec in EDITABLE_FIELDS:
        assert spec.name in accounts.NORMALIZERS, spec.name

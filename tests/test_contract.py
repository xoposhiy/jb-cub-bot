"""What a feature may declare, and what the core refuses to boot with."""
from datetime import datetime, timezone

import pytest
from aiogram.types import Chat, Document, Message, PhotoSize

from jbcub_bot.core.contract import (
    ANY,
    DOCUMENT,
    PHOTO,
    TEXT,
    ContractError,
    Description,
    Guard,
    Registry,
    call_handler,
)
from jbcub_bot.core.dialogs import DialogHandle
from jbcub_bot.core.models import Role


async def _noop(message):
    return None


def _api(registry, feature="directory"):
    """A described feature -- the only registration that validates at all."""
    bot = registry.api_for(feature)
    bot.describe("📒", "Directory", "Find classmates.")
    return bot


def _message(**kwargs) -> Message:
    return Message(message_id=1, date=datetime.now(timezone.utc),
                   chat=Chat(id=1, type="private"), **kwargs)


# --- what a feature declares --------------------------------------------------

def test_describe_becomes_the_features_heading():
    registry = Registry()
    _api(registry)
    [feature] = registry.features()
    assert feature.name == "directory"
    assert feature.description == Description("📒", "Directory",
                                              "Find classmates.")


def test_command_records_its_spec_and_hands_the_handler_back():
    registry = Registry()
    bot = _api(registry)
    returned = bot.command("me", "Show your profile.", usage="<ref>")(_noop)
    assert returned is _noop
    [spec] = registry.features()[0].commands
    assert (spec.feature, spec.name, spec.description, spec.usage) == \
        ("directory", "me", "Show your profile.", "<ref>")
    assert spec.listed is True
    assert spec.guard == Guard()
    assert spec.handler is _noop


def test_command_works_as_a_decorator():
    registry = Registry()
    bot = _api(registry)

    @bot.command("sync", "Refresh the roster.", role=Role.ADMIN, listed=False)
    async def handler(message):
        pass

    [spec] = registry.features()[0].commands
    assert spec.guard == Guard(role=Role.ADMIN)
    assert spec.listed is False
    assert spec.handler is handler


def test_message_defaults_to_text_and_no_description():
    registry = Registry()
    bot = _api(registry)
    bot.message(at=100)(_noop)
    [spec] = registry.features()[0].messages
    assert spec.at == 100
    assert spec.when is TEXT
    assert spec.description == ""
    assert spec.guard == Guard()
    assert spec.handler is _noop


def test_button_records_its_key_and_guard():
    registry = Registry()
    bot = _api(registry)
    bot.button("dir:admin", role=Role.ADMIN)(_noop)
    [spec] = registry.features()[0].buttons
    assert (spec.feature, spec.key) == ("directory", "dir:admin")
    assert spec.guard == Guard(role=Role.ADMIN)
    assert spec.handler is _noop


def test_dialog_is_a_call_and_returns_a_handle_to_what_it_recorded():
    registry = Registry()
    bot = _api(registry)

    async def on_cancel(message):
        pass

    handle = bot.dialog("edit", on_text=_noop, on_cancel=on_cancel)
    [spec] = registry.features()[0].dialogs
    # The handle is what the feature keeps -- the spec is the core's business.
    assert handle == DialogHandle("directory", "edit")
    assert handle.state == "directory:edit"
    assert (spec.feature, spec.name) == ("directory", "edit")
    assert spec.on_text is _noop
    assert spec.on_cancel is on_cancel


def test_dialog_may_have_no_cancel_hook():
    registry = Registry()
    bot = _api(registry)
    bot.dialog("edit", on_text=_noop)
    [spec] = registry.features()[0].dialogs
    assert spec.on_cancel is None


def test_a_callable_note_is_stored_unevaluated():
    registry = Registry()
    bot = _api(registry)
    calls = []

    def render(principal):
        calls.append(principal)
        return "The base currently holds: 3 notes."

    bot.note(render, role=Role.ADMIN)
    [spec] = registry.features()[0].notes
    assert spec.text is render
    assert spec.guard == Guard(role=Role.ADMIN)
    assert calls == [], "the note was rendered at registration time"


def test_a_plain_string_note_is_stored_as_is():
    registry = Registry()
    bot = _api(registry)
    bot.note("Ask me anything.")
    [spec] = registry.features()[0].notes
    assert spec.text == "Ask me anything."
    assert spec.guard == Guard()


def test_the_api_can_see_every_feature():
    registry = Registry()
    _api(registry, "directory")
    _api(registry, "help")
    bot = registry.api_for("directory")
    assert [f.name for f in bot.features()] == ["directory", "help"]


# --- what the registry exposes to the core ------------------------------------

def test_commands_are_keyed_by_bare_name():
    registry = Registry()
    _api(registry).command("me", "Show your profile.")(_noop)
    assert list(registry.commands()) == ["me"]
    assert registry.commands()["me"].handler is _noop


def test_chain_is_sorted_by_position_not_by_registration_order():
    registry = Registry()
    late = _api(registry, "kb")
    late.message(at=200)(_noop)
    early = _api(registry, "directory")
    early.message(at=100)(_noop)
    early.message(at=150)(_noop)
    assert [spec.at for spec in registry.chain()] == [100, 150, 200]


def test_features_are_listed_in_registration_order():
    registry = Registry()
    _api(registry, "zeta")
    _api(registry, "alpha")
    assert [f.name for f in registry.features()] == ["zeta", "alpha"]


def test_buttons_are_collected_across_features():
    registry = Registry()
    _api(registry, "directory").button("dir:admin")(_noop)
    _api(registry, "kb").button("kb:page")(_noop)
    assert [spec.key for spec in registry.buttons()] == ["dir:admin", "kb:page"]


def test_dialogs_are_keyed_by_state_name():
    registry = Registry()
    _api(registry, "directory").dialog("edit", on_text=_noop)
    assert list(registry.dialogs()) == ["directory:edit"]


# --- validate() ----------------------------------------------------------------

def test_a_registration_that_is_fine_validates_silently():
    registry = Registry()
    bot = _api(registry)
    bot.command("me", "Show your profile.")(_noop)
    bot.command("sync", "Refresh the roster.", role=Role.ADMIN)(_noop)
    bot.message(at=100, when=ANY, description="A name finds a classmate.")(_noop)
    bot.button("dir:admin")(_noop)
    bot.dialog("edit", on_text=_noop)
    bot.note("Anything else is a name search.", public=True)
    registry.validate()


def test_a_feature_without_describe_is_refused():
    registry = Registry()
    registry.api_for("directory")
    with pytest.raises(ContractError) as err:
        registry.validate()
    assert "directory" in str(err.value)
    assert "describe" in str(err.value)


def test_two_commands_of_one_name_are_refused():
    registry = Registry()
    _api(registry, "directory").command("cancel", "Stop.")(_noop)
    _api(registry, "kb").command("cancel", "Stop asking.")(_noop)
    with pytest.raises(ContractError) as err:
        registry.validate()
    assert "cancel" in str(err.value)
    assert "directory" in str(err.value)
    assert "kb" in str(err.value)


def test_a_command_without_a_description_is_refused():
    registry = Registry()
    _api(registry).command("me", "", listed=False)(_noop)
    with pytest.raises(ContractError) as err:
        registry.validate()
    assert "me" in str(err.value)
    assert "directory" in str(err.value)


def test_two_buttons_of_one_key_are_refused():
    registry = Registry()
    _api(registry, "directory").button("dir:admin")(_noop)
    _api(registry, "kb").button("dir:admin")(_noop)
    with pytest.raises(ContractError) as err:
        registry.validate()
    assert "dir:admin" in str(err.value)
    assert "directory" in str(err.value)
    assert "kb" in str(err.value)


def test_two_message_handlers_at_one_position_are_refused():
    registry = Registry()
    _api(registry, "directory").message(at=100)(_noop)
    _api(registry, "kb").message(at=100)(_noop)
    with pytest.raises(ContractError) as err:
        registry.validate()
    assert "100" in str(err.value)
    assert "directory" in str(err.value)
    assert "kb" in str(err.value)


def test_two_dialogs_of_one_name_in_one_feature_are_refused():
    registry = Registry()
    bot = _api(registry)
    bot.dialog("edit", on_text=_noop)
    bot.dialog("edit", on_text=_noop)
    with pytest.raises(ContractError) as err:
        registry.validate()
    assert "edit" in str(err.value)
    assert "directory" in str(err.value)


def test_the_same_dialog_name_in_two_features_is_fine():
    registry = Registry()
    _api(registry, "directory").dialog("edit", on_text=_noop)
    _api(registry, "kb").dialog("edit", on_text=_noop)
    registry.validate()


def test_public_together_with_role_is_refused():
    registry = Registry()
    _api(registry).command("sync", "Refresh.", public=True, role=Role.ADMIN)(_noop)
    with pytest.raises(ContractError) as err:
        registry.validate()
    assert "public" in str(err.value)
    assert "sync" in str(err.value)
    assert "directory" in str(err.value)


def test_public_together_with_role_is_refused_on_a_note_too():
    registry = Registry()
    _api(registry).note("Anything.", public=True, role=Role.ADMIN)
    with pytest.raises(ContractError) as err:
        registry.validate()
    assert "public" in str(err.value)
    assert "directory" in str(err.value)


def test_a_handler_asking_for_an_uninjectable_parameter_is_refused():
    registry = Registry()

    async def handler(message, principl):  # a typo for `principal`
        pass

    _api(registry).command("me", "Show your profile.")(handler)
    with pytest.raises(ContractError) as err:
        registry.validate()
    assert "principl" in str(err.value)
    assert "directory" in str(err.value)
    assert "principal" in str(err.value), "the message should list what it can inject"


def test_an_uninjectable_parameter_is_caught_on_every_kind():
    async def handler(message, nonsense):
        pass

    for declare in (
        lambda bot: bot.message(at=100)(handler),
        lambda bot: bot.button("dir:admin")(handler),
        lambda bot: bot.dialog("edit", on_text=handler),
        lambda bot: bot.dialog("edit", on_text=_noop, on_cancel=handler),
    ):
        registry = Registry()
        declare(_api(registry))
        with pytest.raises(ContractError) as err:
            registry.validate()
        assert "nonsense" in str(err.value)


def test_every_injectable_name_is_accepted():
    registry = Registry()

    async def handler(message, principal, session, bot, impersonator, dialog,
                      arg, oplog):
        pass

    _api(registry).command("me", "Show your profile.")(handler)
    registry.validate()


# --- predicates ----------------------------------------------------------------

def test_text_accepts_text_and_nothing_else():
    assert TEXT(_message(text="Ivan")) is True
    assert TEXT(_message(caption="Ivan")) is False


def test_text_accepts_a_command_because_the_chain_never_sees_one():
    assert TEXT(_message(text="/me")) is True


def test_photo_accepts_a_photo_and_nothing_else():
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]
    assert PHOTO(_message(photo=photo)) is True
    assert PHOTO(_message(text="Ivan")) is False


def test_document_accepts_a_document_and_nothing_else():
    document = Document(file_id="f", file_unique_id="u")
    assert DOCUMENT(_message(document=document)) is True
    assert DOCUMENT(_message(text="Ivan")) is False


def test_any_accepts_everything():
    assert ANY(_message(text="Ivan")) is True
    assert ANY(_message()) is True


# --- call_handler ---------------------------------------------------------------

async def test_the_event_goes_first_whatever_the_parameter_is_named():
    seen = []

    async def handler(cb):
        seen.append(cb)

    await call_handler(handler, "EVENT", principal=None, session="S")
    assert seen == ["EVENT"]


async def test_only_declared_parameters_are_injected():
    seen = {}

    async def handler(message, principal, arg):
        seen.update(principal=principal, arg=arg)

    await call_handler(handler, "EVENT", principal="P", session="S", arg="2",
                       bot="B")
    assert seen == {"principal": "P", "arg": "2"}


async def test_a_declared_parameter_with_a_default_is_still_injected():
    seen = {}

    async def handler(message, arg=""):
        seen["arg"] = arg

    await call_handler(handler, "EVENT", arg="2")
    assert seen == {"arg": "2"}


async def test_the_handlers_return_value_comes_back():
    async def declines(message):
        return False

    assert await call_handler(declines, "EVENT", principal=None) is False

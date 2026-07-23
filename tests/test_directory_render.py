from sdt_bot.features.directory.render import render_profile
from sdt_bot.core.models import Role, User


def test_render_includes_visible_and_omits_hidden():
    viewer = User(name="V", role=Role.STUDENT, primary_cohort="2024")
    target = User(name="Ivan Ivanov", role=Role.STUDENT, primary_cohort="2024",
                  handle_observed="ivanov", gmail="i@gmail.com",
                  visibility={"gmail": "nobody"})
    text = render_profile(viewer, target)
    assert "Ivan Ivanov" in text
    assert "ivanov" in text
    assert "i@gmail.com" not in text  # hidden by visibility

from jbcub_bot.features.directory.render import render_profile
from jbcub_bot.core.models import Role, User


def test_render_includes_visible_and_omits_hidden():
    viewer = User(first_name="V", last_name="", role=Role.STUDENT,
                  primary_cohort="2024")
    target = User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                  primary_cohort="2024",
                  handle_observed="ivanov", gmail="i@gmail.com",
                  visibility={"gmail": "nobody"})
    text = render_profile(viewer, target)
    assert "Name: Ivan Ivanov" in text
    assert "ivanov" in text
    assert "i@gmail.com" not in text  # hidden by visibility

"""What the check says, and what it deliberately keeps quiet about.

Every complaint here has to name something the reader would otherwise see,
because the agent pays a whole extra round trip for each one and is free to
ignore it anyway. A check that fires on a matter of taste is worse than no
check: it spends money and teaches the agent that the complaints are noise.
"""
from jbcub_bot.features.kb import validate


def test_a_clean_answer_draws_no_complaint():
    answer = ("Retakes are allowed once.\n"
              "<blockquote>A failed module <b>may be retaken once</b>."
              "</blockquote>\n"
              "📄 Policies for Bachelor Studies v8 — §III.4, pp. 18–20")

    assert validate.complaints(answer) == []


def test_a_repository_path_is_complained_about():
    found = validate.complaints("See kb/policies/exams.md for the rule.")

    assert len(found) == 1
    assert "kb/policies/exams.md" in found[0]


def test_every_leaked_path_is_named_once():
    found = validate.complaints("kb/a.md and kb/b.md and kb/a.md again")

    assert len(found) == 1
    assert "kb/a.md" in found[0] and "kb/b.md" in found[0]


def test_an_unclosed_tag_is_complained_about():
    """Telegram rejects the whole message over this, so the reader gets
    nothing at all — the complaint most worth a second pass."""
    found = validate.complaints("<b>Bold and never closed.")

    assert any("opened and never closed" in c for c in found)


def test_a_stray_closing_tag_is_complained_about():
    found = validate.complaints("Text</b> and more.")

    assert any("never opened" in c for c in found)


def test_a_tag_telegram_does_not_know_is_complained_about():
    found = validate.complaints("A <div>block</div> here.")

    assert any("<div> is not a tag Telegram accepts" in c for c in found)
    assert sum("not a tag" in c for c in found) == 1, "said once, not per tag"


def test_crossed_tags_are_complained_about_by_name():
    """Telegram parses tags as a stack, so this is another whole-message
    rejection. The complaint has to say which tag to close first, or the agent
    has nothing to act on."""
    found = validate.complaints("<b><i>x</b></i>")

    assert len(found) == 1
    assert "Close <i> first" in found[0]


def test_an_answer_over_telegrams_limit_is_complained_about():
    found = validate.complaints("x" * (validate.LIMIT + 1))

    assert any("Telegram takes" in c for c in found)


def test_the_feedback_tells_the_agent_it_may_disagree():
    """An agent bullied into rewriting a good answer is a worse outcome than
    the thing being complained about."""
    text = validate.feedback(["The answer names kb/a.md."])

    assert "kb/a.md" in text
    assert "advice, not a rule" in text
    assert "unchanged" in text, "and how to stand its ground"

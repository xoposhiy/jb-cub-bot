from jbcub_bot.core import gradebook, sheets
from jbcub_bot.features.directory import grades, sync_diagnostics
from jbcub_bot.features.directory.sync_diagnostics import (
    MAX_REPORT_TEXT,
    CohortOutcome,
    IssueGroup,
    RightsOutcome,
    build_issue_groups,
    build_rights_issue_groups,
    counted,
    render_cohort,
    render_final,
)


def _grades_report() -> grades.GradesSyncReport:
    return grades.GradesSyncReport(
        source_people=5,
        matched_people=2,
        cells=17,
        no_roster_match=["Aliev Rufat", "Rosa Maria"],
        ambiguous_roster_match=[
            grades.CountedName(name="Lee Alex", count=2)
        ],
        missing_gradebook_rows=["Ivan Petrov"],
        duplicate_rows=[
            grades.CountedName(name="John Smith", count=3)
        ],
        ignored_columns=[
            gradebook.IgnoredColumn(
                index=51,
                label="Credits Failed after make-up",
            )
        ],
    )


def test_problem_copy_is_grouped_and_directional():
    groups = build_issue_groups(
        roster=sheets.ReconcileReport(
            differences=[
                sheets.FieldDifference(
                    key="30000001",
                    field="telegram",
                    sheet_value="ivan_old",
                    profile_value="ivan_new",
                )
            ],
            duplicates=[
                sheets.DuplicateKey(value="30000009", rows=2)
            ],
        ),
        grades_report=_grades_report(),
        departed=[
            sheets.DepartedUser(
                matriculation="30000010",
                full_name="Eve Expelled",
            )
        ],
    )

    outcome = CohortOutcome(
        cohort="sdt-2025-2028",
        roster_students=33,
        ignored_roster_rows=16,
        gradebook=_grades_report(),
        gradebook_error=None,
        issues=groups,
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )
    rendered = render_cohort(outcome)
    assert rendered.document_bytes is None
    assert rendered.text is not None
    assert "Gradebook rows without a roster match (2)" in rendered.text
    assert "Aliev Rufat" in rendered.text
    assert "Roster students without a Gradebook row (1)" in rendered.text
    assert "Duplicate Gradebook rows (1)" in rendered.text
    assert "John Smith — 3 rows" in rendered.text
    assert "Ambiguous roster names (1)" in rendered.text
    assert "Lee Alex — 2 roster profiles" in rendered.text
    assert "Columns outside a semester (1)" in rendered.text
    assert "AZ — Credits Failed after make-up" in rendered.text
    assert "Profile values differing from the sheet (1)" in rendered.text
    assert "sheet @ivan_old; profile @ivan_new" in rendered.text
    assert "Newly marked as departed (1)" in rendered.text
    assert "16 historical rows below the roster separator were ignored" in rendered.text
    assert "unmatched=" not in rendered.text
    assert "dup=" not in rendered.text
    assert "drift=" not in rendered.text


def test_rights_duplicates_name_the_handle_source():
    groups = build_rights_issue_groups(sheets.ReconcileReport(
        duplicates=[sheets.DuplicateKey(value="boss", rows=2)]
    ))

    assert groups == (
        IssueGroup(
            title="Duplicate Rights handles",
            effect="These Rights rows resolve to the same profile.",
            action="Correct the duplicate Telegram handles on the Rights tab",
            items=("boss — 2 rows",),
        ),
    )


def test_issue_items_use_singular_count_grammar():
    groups = build_issue_groups(
        sheets.ReconcileReport(
            duplicates=[sheets.DuplicateKey(value="30000009", rows=1)]
        ),
        grades.GradesSyncReport(
            duplicate_rows=[grades.CountedName("John Smith", 1)],
            ambiguous_roster_match=[grades.CountedName("Lee Alex", 1)],
        ),
        [],
    )

    assert groups[0].items == ("John Smith — 1 row",)
    assert groups[1].items == ("Lee Alex — 1 roster profile",)
    assert groups[2].items == ("30000009 — 1 row",)


def test_counted_uses_correct_english_forms():
    assert counted(1, "roster student") == "1 roster student"
    assert counted(2, "roster student") == "2 roster students"
    assert counted(1, "person", "people") == "1 person"
    assert counted(2, "person", "people") == "2 people"


def test_long_report_becomes_one_complete_text_document():
    report = grades.GradesSyncReport(
        source_people=80,
        matched_people=0,
        no_roster_match=[
            f"Student {index:03d} " + ("X" * 80)
            for index in range(80)
        ],
    )
    outcome = CohortOutcome(
        cohort="sdt-2025-2028",
        roster_students=33,
        ignored_roster_rows=0,
        gradebook=report,
        gradebook_error=None,
        issues=build_issue_groups(
            roster=sheets.ReconcileReport(),
            grades_report=report,
            departed=[],
        ),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )

    rendered = render_cohort(outcome)

    assert rendered.text is None
    assert rendered.document_name == "sync-sdt-2025-2028.txt"
    assert rendered.document_bytes is not None
    body = rendered.document_bytes.decode("utf-8")
    assert "Student 000" in body
    assert "Student 079" in body
    assert "and 1 more" not in body
    assert rendered.caption is not None
    assert len(rendered.caption) < 1024


def test_final_summary_names_every_cohort_and_the_rights_count():
    healthy = CohortOutcome(
        cohort="sdt-2025-2028",
        roster_students=33,
        ignored_roster_rows=0,
        gradebook=grades.GradesSyncReport(
            source_people=49,
            matched_people=33,
            cells=1247,
        ),
        gradebook_error=None,
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )
    kept = CohortOutcome(
        cohort="sdt-2024-2027",
        roster_students=36,
        ignored_roster_rows=0,
        gradebook=None,
        gradebook_error="Gradebook header row not found",
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/BBB",
    )
    rights = RightsOutcome(
        staff_records=6,
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/RIGHTS",
    )

    text = render_final([healthy, kept], rights)

    assert text.startswith("⚠️ Sync completed with warnings")
    assert "sdt-2025-2028 — 33 roster students; 33 Gradebook rows matched" in text
    assert (
        "sdt-2024-2027 — 36 roster students; "
        "grades not updated, previous data kept"
    ) in text
    assert "Rights: 6 staff records" in text
    assert "1 of 2" not in text


def test_oversized_final_report_becomes_one_complete_text_document():
    cohort = CohortOutcome(
        cohort="2024",
        roster_students=1,
        ignored_roster_rows=0,
        gradebook=grades.GradesSyncReport(source_people=1, matched_people=1),
        gradebook_error=None,
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )
    items = tuple(
        f"staff-{index:03d} — 2 rows"
        for index in range(400)
    )
    rights = RightsOutcome(
        staff_records=400,
        issues=(
            IssueGroup(
                title="Duplicate Rights handles",
                effect="These Rights rows resolve to the same profile.",
                action="Correct the duplicate Telegram handles on the Rights tab",
                items=items,
            ),
        ),
        source_url="https://docs.google.com/spreadsheets/d/RIGHTS",
    )
    complete_text = render_final([cohort], rights)

    rendered = sync_diagnostics.render_final_report([cohort], rights)

    assert len(complete_text) > MAX_REPORT_TEXT
    assert rendered.text is None
    assert rendered.document_name == "sync-final.txt"
    assert rendered.document_bytes == complete_text.encode("utf-8")
    assert rendered.caption is not None
    assert rendered.caption.startswith("⚠️ Sync completed with warnings")
    assert len(rendered.caption) < 1024


def test_final_summary_says_when_rights_were_not_updated():
    cohort = CohortOutcome(
        cohort="2024",
        roster_students=1,
        ignored_roster_rows=0,
        gradebook=grades.GradesSyncReport(source_people=1, matched_people=1),
        gradebook_error=None,
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )
    rights = RightsOutcome(
        staff_records=6,
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/RIGHTS",
        updated=False,
    )

    text = render_final([cohort], rights)

    assert text.startswith("⚠️ Sync completed with warnings")
    assert "Rights: not updated; previous data kept" in text
    assert "Rights: 6 staff records" not in text


def test_gradebook_error_is_the_first_issue_after_the_facts():
    outcome = CohortOutcome(
        cohort="sdt-2025-2028",
        roster_students=1,
        ignored_roster_rows=0,
        gradebook=None,
        gradebook_error="Gradebook header row not found",
        issues=(IssueGroup("Roster issue", "Effect.", "Fix", ("item",)),),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )

    rendered = render_cohort(outcome)

    assert rendered.text is not None
    assert rendered.text.index("Gradebook was not updated (1)") < rendered.text.index(
        "Roster issue (1)"
    )


def test_report_at_the_character_boundary_stays_a_text_message():
    facts = (
        "⚠️ boundary processed\n\n"
        "Roster: 1 student\n"
        "Gradebook: 1 of 1 row matched · 0 cells imported"
    )
    group_prefix = "\n\nx (1)\ny z:\n• "
    outcome = CohortOutcome(
        cohort="boundary",
        roster_students=1,
        ignored_roster_rows=0,
        gradebook=grades.GradesSyncReport(source_people=1, matched_people=1),
        gradebook_error=None,
        issues=(IssueGroup("x", "y", "z", ("X" * (
            MAX_REPORT_TEXT - len(facts) - len(group_prefix)
        ),)),),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )
    rendered = render_cohort(outcome)

    assert rendered.text is not None
    assert len(rendered.text) == MAX_REPORT_TEXT


def test_report_one_character_over_the_boundary_becomes_a_document():
    facts = (
        "⚠️ boundary processed\n\n"
        "Roster: 1 student\n"
        "Gradebook: 1 of 1 row matched · 0 cells imported"
    )
    group_prefix = "\n\nx (1)\ny z:\n• "
    outcome = CohortOutcome(
        cohort="boundary",
        roster_students=1,
        ignored_roster_rows=0,
        gradebook=grades.GradesSyncReport(source_people=1, matched_people=1),
        gradebook_error=None,
        issues=(IssueGroup("x", "y", "z", ("X" * (
            MAX_REPORT_TEXT + 1 - len(facts) - len(group_prefix)
        ),)),),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )
    rendered = render_cohort(outcome)

    assert rendered.text is None
    assert rendered.document_bytes is not None
    assert len(rendered.document_bytes.decode("utf-8")) == MAX_REPORT_TEXT + 1


def test_document_filename_is_safe_for_an_empty_or_punctuated_cohort_name():
    report = grades.GradesSyncReport(
        no_roster_match=["A" * (MAX_REPORT_TEXT + 1)],
    )
    outcome = CohortOutcome(
        cohort=" /// ",
        roster_students=0,
        ignored_roster_rows=0,
        gradebook=report,
        gradebook_error=None,
        issues=build_issue_groups(sheets.ReconcileReport(), report, []),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )

    rendered = render_cohort(outcome)

    assert rendered.document_name == "sync-cohort.txt"


def test_final_warning_predicates_include_rights_issues_and_completion_note():
    cohort = CohortOutcome(
        cohort="sdt-2025-2028",
        roster_students=1,
        ignored_roster_rows=0,
        gradebook=grades.GradesSyncReport(source_people=1, matched_people=1),
        gradebook_error=None,
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )
    rights = RightsOutcome(
        staff_records=1,
        issues=(IssueGroup("x", "y", "z", ("item",)),),
        source_url="https://docs.google.com/spreadsheets/d/RIGHTS",
    )

    assert render_final([cohort], rights).startswith("⚠️ Sync completed with warnings")
    assert (
        render_final([cohort], RightsOutcome(1, (), "url"), "Note").splitlines()[0]
        == "⚠️ Sync partially completed"
    )


def test_partial_completion_note_is_appended_exactly():
    cohort = CohortOutcome(
        cohort="2024",
        roster_students=1,
        ignored_roster_rows=0,
        gradebook=grades.GradesSyncReport(source_people=1, matched_people=1),
        gradebook_error=None,
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )
    note = (
        "The processed cohorts above remain updated; "
        "the remaining sources were not completed."
    )

    text = render_final(
        [cohort],
        RightsOutcome(
            staff_records=0,
            issues=(),
            source_url="https://docs.google.com/spreadsheets/d/RIGHTS",
        ),
        completion_note=note,
    )

    assert text.splitlines()[0] == "⚠️ Sync partially completed"
    assert text.endswith(note)


def test_document_caption_and_filename_bound_a_very_long_cohort_name():
    cohort = "sdt-" + ("2025-2028-" * 500)
    outcome = CohortOutcome(
        cohort=cohort,
        roster_students=1,
        ignored_roster_rows=0,
        gradebook=grades.GradesSyncReport(source_people=1, matched_people=1),
        gradebook_error=None,
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )

    rendered = render_cohort(outcome)

    assert rendered.text is None
    assert rendered.document_bytes is not None
    assert cohort in rendered.document_bytes.decode("utf-8")
    assert rendered.caption is not None
    assert len(rendered.caption) < 1024
    assert rendered.document_name is not None
    assert rendered.document_name.endswith(".txt")
    assert len(rendered.document_name) <= 255
    assert rendered.document_name != "sync-.txt"
    assert all(char.isascii() and (char.isalnum() or char in "._-")
               for char in rendered.document_name)


def test_missing_gradebook_without_error_warns_the_cohort_and_final_summary():
    outcome = CohortOutcome(
        cohort="sdt-2025-2028",
        roster_students=1,
        ignored_roster_rows=0,
        gradebook=None,
        gradebook_error=None,
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )

    cohort_report = render_cohort(outcome)
    final_report = render_final([outcome], RightsOutcome(1, (), "url"))

    assert cohort_report.text is not None
    assert cohort_report.text.startswith("⚠️")
    assert final_report.startswith("⚠️ Sync completed with warnings")


def test_group_and_matched_counts_are_routed_through_counted(monkeypatch):
    calls = []
    original = sync_diagnostics.counted

    def observe(count, singular, plural=None):
        calls.append((count, singular, plural))
        return original(count, singular, plural)

    monkeypatch.setattr(sync_diagnostics, "counted", observe)
    outcome = CohortOutcome(
        cohort="sdt-2025-2028",
        roster_students=4,
        ignored_roster_rows=0,
        gradebook=grades.GradesSyncReport(source_people=7, matched_people=2),
        gradebook_error=None,
        issues=(IssueGroup("Problem", "Effect.", "Fix", ("item",)),),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )

    rendered = render_cohort(outcome)

    assert rendered.text is not None
    assert "Gradebook: 2 of 7 rows matched" in rendered.text
    assert "Problem (1)" in rendered.text
    assert (2, "matched Gradebook row", None) in calls
    assert (1, "item", None) in calls


def test_final_rights_groups_are_separated_by_two_newlines():
    cohort = CohortOutcome(
        cohort="sdt-2025-2028",
        roster_students=1,
        ignored_roster_rows=0,
        gradebook=grades.GradesSyncReport(source_people=1, matched_people=1),
        gradebook_error=None,
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )
    rights = RightsOutcome(
        staff_records=1,
        issues=(
            IssueGroup("First", "Effect.", "Fix", ("one",)),
            IssueGroup("Second", "Effect.", "Fix", ("two",)),
        ),
        source_url="https://docs.google.com/spreadsheets/d/RIGHTS",
    )

    text = render_final([cohort], rights)

    assert "• one\n\nSecond (1)" in text

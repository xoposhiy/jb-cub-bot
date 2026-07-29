"""Pure rendering of structured /sync diagnostics."""

from dataclasses import dataclass
import re

from jbcub_bot.core import gradebook, sheets
from jbcub_bot.features.directory import grades


MAX_REPORT_TEXT = 3900
MAX_CAPTION_TEXT = 1023
MAX_DOCUMENT_NAME = 255


@dataclass(frozen=True)
class IssueGroup:
    title: str
    effect: str
    action: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class CohortOutcome:
    cohort: str
    roster_students: int
    ignored_roster_rows: int
    gradebook: grades.GradesSyncReport | None
    gradebook_error: str | None
    issues: tuple[IssueGroup, ...]
    source_url: str


@dataclass(frozen=True)
class RightsOutcome:
    staff_records: int
    issues: tuple[IssueGroup, ...]
    source_url: str
    updated: bool = True


@dataclass(frozen=True)
class RenderedReport:
    text: str | None
    caption: str | None
    document_name: str | None
    document_bytes: bytes | None


def counted(count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else (plural or singular + "s")
    return f"{count} {word}"


def _bare_count(count: int, singular: str) -> str:
    """Use the shared grammar helper where the template needs a bare number."""
    return counted(count, singular).split(" ", 1)[0]


def _differences_group(
    differences: list[sheets.FieldDifference],
) -> IssueGroup:
    labels = {
        "telegram": "Telegram",
        "github": "GitHub",
        "codeforces": "Codeforces",
    }

    def item(difference: sheets.FieldDifference) -> str:
        label = labels.get(
            difference.field, difference.field.replace("_", " ").title()
        )
        prefix = "@" if difference.field == "telegram" else ""
        return (
            f"{difference.key} — {label}: "
            f"sheet {prefix}{difference.sheet_value}; "
            f"profile {prefix}{difference.profile_value}"
        )

    return IssueGroup(
        title="Profile values differing from the sheet",
        effect="The sheet and profile have different values.",
        action="If the profile value is current, update the sheet",
        items=tuple(item(difference) for difference in differences),
    )


def build_issue_groups(
    roster: sheets.ReconcileReport,
    grades_report: grades.GradesSyncReport | None,
    departed: list[sheets.DepartedUser],
) -> tuple[IssueGroup, ...]:
    groups: list[IssueGroup] = []

    if grades_report is not None:
        if grades_report.missing_gradebook_rows:
            groups.append(IssueGroup(
                title="Roster students without a Gradebook row",
                effect="No grades were found for these current roster students.",
                action="Add or correct their row on the Gradebook tab",
                items=tuple(grades_report.missing_gradebook_rows),
            ))
            if grades_report.no_roster_match:
                groups.append(IssueGroup(
                    title="Gradebook rows without a roster match",
                    effect="These Gradebook rows were not imported.",
                    action=(
                        "Compare these names with the missing current roster "
                        "students and correct any misspellings"
                    ),
                    items=tuple(grades_report.no_roster_match),
                ))
        if grades_report.duplicate_rows:
            groups.append(IssueGroup(
                title="Duplicate Gradebook rows",
                effect="These grade rows were skipped.",
                action="Keep or correct one row for each person",
                items=tuple(
                    f"{item.name} — {counted(item.count, 'row')}"
                    for item in grades_report.duplicate_rows
                ),
            ))
        if grades_report.ambiguous_roster_match:
            groups.append(IssueGroup(
                title="Ambiguous roster names",
                effect="These Gradebook rows were not imported.",
                action="Make the roster names unique before re-running /sync",
                items=tuple(
                    f"{item.name} — {counted(item.count, 'roster profile')}"
                    for item in grades_report.ambiguous_roster_match
                ),
            ))
        if grades_report.unmatchable_roster_rows:
            groups.append(IssueGroup(
                title="Current roster rows cannot receive grades",
                effect=(
                    "Grades could not be assigned for these current roster rows."
                ),
                action="Add the missing roster identity fields and re-run /sync",
                items=tuple(grades_report.unmatchable_roster_rows),
            ))

    if roster.duplicates:
        groups.append(IssueGroup(
            title="Duplicate matriculation numbers",
            effect="These roster rows cannot be safely reconciled.",
            action="Correct the duplicate matriculation numbers in the roster",
            items=tuple(
                f"{item.value} — {counted(item.rows, 'row')}"
                for item in roster.duplicates
            ),
        ))
    if roster.differences:
        groups.append(_differences_group(roster.differences))
    if departed:
        groups.append(IssueGroup(
            title="Newly marked as departed",
            effect="These people can no longer access the bot.",
            action="Restore their roster row if this was unintended",
            items=tuple(
                f"{item.full_name} ({item.matriculation})" for item in departed
            ),
        ))

    if grades_report is not None and grades_report.ignored_columns:
        groups.append(IssueGroup(
            title="Columns outside a semester",
            effect="These columns were not imported.",
            action="Extend a semester header over these columns",
            items=tuple(
                f"{gradebook.sheet_column_name(item.index)} — {item.label}"
                for item in grades_report.ignored_columns
            ),
        ))
    return tuple(groups)


def build_rights_issue_groups(
    report: sheets.ReconcileReport,
) -> tuple[IssueGroup, ...]:
    groups = []
    if report.duplicates:
        groups.append(IssueGroup(
            title="Duplicate Rights handles",
            effect="These Rights rows resolve to the same profile.",
            action="Correct the duplicate Telegram handles on the Rights tab",
            items=tuple(
                f"{item.value} — {counted(item.rows, 'row')}"
                for item in report.duplicates
            ),
        ))
    if report.differences:
        groups.append(_differences_group(report.differences))
    return tuple(groups)


def _format_group(group: IssueGroup) -> str:
    return "\n".join((
        f"{group.title} ({_bare_count(len(group.items), 'item')})",
        f"{group.effect} {group.action}:",
        *(f"• {item}" for item in group.items),
    ))


def _gradebook_fact(outcome: CohortOutcome) -> str:
    if outcome.gradebook_error is not None:
        return "Gradebook: not updated; previous data kept"
    if outcome.gradebook is None:
        return "Gradebook: not available"
    report = outcome.gradebook
    return (
        f"Gradebook: {_bare_count(_current_roster_found(outcome), 'student')} of "
        f"{counted(outcome.roster_students, 'current roster student')} found · "
        f"{counted(report.cells, 'cell')} imported"
    )


def _current_roster_found(outcome: CohortOutcome) -> int:
    if outcome.gradebook is None:
        return 0
    if outcome.gradebook.current_roster_people:
        return outcome.gradebook.current_roster_found
    return max(
        0,
        outcome.roster_students
        - len(outcome.gradebook.missing_gradebook_rows),
    )


def _gradebook_error_group(error: str) -> IssueGroup:
    return IssueGroup(
        title="Gradebook was not updated",
        effect="The previous successful Gradebook data was kept.",
        action="Fix the Gradebook error and re-run /sync",
        items=(error,),
    )


def _cohort_status(outcome: CohortOutcome) -> str:
    return "⚠️" if (
        outcome.issues
        or outcome.gradebook_error is not None
        or outcome.gradebook is None
    ) else "✅"


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[:limit - 1] + "…"


def _document_filename(cohort: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", cohort).strip("-")
    slug_limit = MAX_DOCUMENT_NAME - len("sync-") - len(".txt")
    slug = slug[:slug_limit].rstrip("-") or "cohort"
    return f"sync-{slug}.txt"


def render_cohort(outcome: CohortOutcome) -> RenderedReport:
    sections = [
        f"{_cohort_status(outcome)} {outcome.cohort} processed",
        "\n".join((
            f"Roster: {counted(outcome.roster_students, 'student')}",
            _gradebook_fact(outcome),
        )),
    ]
    if outcome.ignored_roster_rows:
        row_count = counted(outcome.ignored_roster_rows, "historical row")
        verb = "was" if outcome.ignored_roster_rows == 1 else "were"
        sections.append(
            f"{row_count} below the roster separator {verb} ignored"
        )

    groups = list(outcome.issues)
    if outcome.gradebook_error is not None:
        groups.insert(0, _gradebook_error_group(outcome.gradebook_error))
    sections.extend(_format_group(group) for group in groups)
    body = "\n\n".join(sections)
    if len(body) <= MAX_REPORT_TEXT:
        return RenderedReport(body, None, None, None)

    gradebook_fact = _gradebook_fact(outcome)
    status = _cohort_status(outcome)
    caption_tail = "\n".join((
        " processed",
        f"Roster: {counted(outcome.roster_students, 'student')}",
        gradebook_fact,
        f"{counted(len(groups), 'issue group')}; full diagnostics attached.",
    ))
    cohort_limit = MAX_CAPTION_TEXT - len(status) - 1 - len(caption_tail)
    caption = f"{status} {_shorten(outcome.cohort, max(cohort_limit, 0))}{caption_tail}"
    return RenderedReport(
        text=None,
        caption=caption,
        document_name=_document_filename(outcome.cohort),
        document_bytes=body.encode("utf-8"),
    )


def _final_gradebook_status(outcome: CohortOutcome) -> str:
    if outcome.gradebook_error is not None:
        return "grades not updated, previous data kept"
    if outcome.gradebook is None:
        return "Gradebook not available"
    return (
        f"{_bare_count(_current_roster_found(outcome), 'student')} of "
        f"{counted(outcome.roster_students, 'current roster student')} "
        "found in Gradebook"
    )


def _final_heading(
    cohorts: list[CohortOutcome],
    rights: RightsOutcome,
    completion_note: str | None,
) -> str:
    if completion_note is not None:
        return "⚠️ Sync partially completed"
    has_warnings = (
        any(
            outcome.issues
            or outcome.gradebook_error is not None
            or outcome.gradebook is None
            for outcome in cohorts
        )
        or bool(rights.issues)
        or not rights.updated
    )
    return "⚠️ Sync completed with warnings" if has_warnings else "✅ Sync completed"


def render_final(
    cohorts: list[CohortOutcome],
    rights: RightsOutcome,
    completion_note: str | None = None,
) -> str:
    lines = [_final_heading(cohorts, rights, completion_note)]
    lines.extend(
        f"{outcome.cohort} — {counted(outcome.roster_students, 'roster student')}; "
        f"{_final_gradebook_status(outcome)}"
        for outcome in cohorts
    )
    if rights.updated:
        lines.append(f"Rights: {counted(rights.staff_records, 'staff record')}")
    else:
        lines.append("Rights: not updated; previous data kept")
    sections = ["\n".join(lines)]
    sections.extend(_format_group(group) for group in rights.issues)
    if completion_note is not None:
        sections.append(completion_note)
    return "\n\n".join(sections)


def render_final_report(
    cohorts: list[CohortOutcome],
    rights: RightsOutcome,
    completion_note: str | None = None,
) -> RenderedReport:
    body = render_final(cohorts, rights, completion_note)
    if len(body) <= MAX_REPORT_TEXT:
        return RenderedReport(body, None, None, None)

    rights_fact = (
        f"Rights: {counted(rights.staff_records, 'staff record')}"
        if rights.updated
        else "Rights: not updated"
    )
    caption = "\n".join((
        _final_heading(cohorts, rights, completion_note),
        f"Cohorts: {counted(len(cohorts), 'cohort')}",
        rights_fact,
        "Full diagnostics attached.",
    ))
    return RenderedReport(
        text=None,
        caption=caption,
        document_name="sync-final.txt",
        document_bytes=body.encode("utf-8"),
    )

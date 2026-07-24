from jbcub_bot.core.models import Role, User

SUPER_MINIMUM = ("last_name", "first_name", "telegram", "primary_cohort",
                 "role", "status_line")
CONFIGURABLE = ("gmail", "github", "codeforces")
ADMIN_ONLY = ("matriculation", "telegram_id", "birthday", "citizenship",
              "comment")

_DEFAULT_LEVEL = "cohort"


def _cohorts(u: User) -> set:
    cohorts = set(u.past_cohorts or [])
    if u.primary_cohort:
        cohorts.add(u.primary_cohort)
    return cohorts


def are_cohort_mates(a: User, b: User) -> bool:
    return bool(_cohorts(a) & _cohorts(b))


def _telegram(u: User):
    handle = u.handle_observed or u.handle_sheet
    return f"@{handle}" if handle else None


def visible_fields(viewer: User, target: User) -> dict:
    fields: dict = {}

    # Super-minimum: always visible to any student/teacher/admin.
    fields["last_name"] = target.last_name
    fields["first_name"] = target.first_name
    fields["telegram"] = _telegram(target)
    fields["primary_cohort"] = target.primary_cohort
    fields["role"] = target.role
    if target.status_line:
        fields["status_line"] = target.status_line

    is_admin = viewer.role is Role.ADMIN
    is_teacher = viewer.role is Role.TEACHER
    mates = are_cohort_mates(viewer, target)

    for field in CONFIGURABLE:
        value = getattr(target, field)
        if value is None:
            continue
        # Staff override: teachers/admins see configurable fields regardless.
        if is_admin or is_teacher:
            fields[field] = value
            continue
        level = (target.visibility or {}).get(field, _DEFAULT_LEVEL)
        if level == "all_students":
            fields[field] = value
        elif level == "cohort" and mates:
            fields[field] = value
        # level == "nobody" -> skip

    if is_admin:
        for field in ADMIN_ONLY:
            fields[field] = getattr(target, field)

    return fields

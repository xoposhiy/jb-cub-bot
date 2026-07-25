# Admin impersonation (`/as`) — design

**Date:** 2026-07-25
**Status:** Approved for planning

## Goal

Let an admin see the bot exactly as a given student sees it. An admin runs:

```
/as <ref> <query>
```

`<query>` is processed as if the referenced user had sent it — same handlers,
same visibility rules — and the output is delivered back into the admin's chat.

Examples:

- `/as 30000001 /me` → renders that student's own profile with **student**
  visibility (not the admin's all-fields view).
- `/as 30000001 /cohort` → that student's cohort list.
- `/as 30000001 Ivanov` → a free-text search run with the student as viewer.

## Scope decisions (from brainstorming)

- **Full impersonation of any input.** `<query>` may be any command or free
  text; it is re-routed through the real dispatcher, not a reimplemented subset.
  This keeps behaviour truthful and drift-free as features are added.
- **Reference resolution: matriculation first, then telegram_id.** Look up
  `User.matriculation == ref`; if not found and `ref.isdigit()`, look up
  `User.telegram_id == int(ref)`. Matriculation is the stable student key, so it
  wins even when numeric.

## Approach

Re-feed a copy of the message (with the query text) back through the aiogram
dispatcher, and make the principal middleware impersonation-aware. This is the
only approach that reuses every real handler without duplicating aiogram's
routing.

Rejected alternatives:

- **Shared dispatch/registry** mapping command → handler manually: duplicates
  routing and drifts every time a command is added.
- **Contextvar impersonation:** hidden global state, fragile under async
  re-entrancy.

## Components

### 1. `identity.find_impersonation_target(session, ref) -> User | None`

Pure, session-based resolver.

```python
def find_impersonation_target(session, ref: str) -> User | None:
    user = session.scalar(select(User).where(User.matriculation == ref))
    if user is not None:
        return user
    if ref.isdigit():
        return session.scalar(
            select(User).where(User.telegram_id == int(ref))
        )
    return None
```

### 2. `PrincipalMiddleware` — impersonation-aware

Resolve the real principal exactly as today (telegram_id → handle claim →
bootstrap). Then:

- If `data.get("impersonate_ref")` is set **and** the real principal is an
  admin: resolve the target via `find_impersonation_target` in *this* session,
  set `data["principal"] = target` (may be `None`), and stash
  `data["impersonator"] = <real admin principal>`.
- Otherwise: `data["principal"] = <real principal>` (unchanged behaviour).

The middleware is the single principal authority, so it performs the swap and
enforces admin-only server-side. Resolving the target in the middleware's own
session (not the caller's) keeps ORM objects session-consistent across the
re-feed.

### 3. New feature package `src/jbcub_bot/features/impersonate/`

Exports `router` + `manifest` (auto-discovered by the loader; no central edits).

`manifest`: `name="impersonate"`, `commands=["as"]`, `min_role=Role.ADMIN`,
help text describing the command. (As with `directory`, `min_role` is
descriptive; the handler enforces the check itself, matching `cmd_sync`.)

Handler `cmd_as(message, principal, session, bot, dispatcher, command)`:

1. Deny if `principal is None or principal.role is not Role.ADMIN`
   → reply `"Admins only."` and return.
2. Parse `command.args`: split once on whitespace into `ref` and `query`
   (both stripped). If `command.args` is empty, or `query` is empty
   → reply usage: `"Usage: /as <matriculation|telegram_id> <query>"`.
3. `target = identity.find_impersonation_target(session, ref)`.
   If `None` → reply `"No user found for {ref}."` and return.
4. Reply header `f"👤 Showing as {target.full_name}:"`.
5. Re-feed:
   ```python
   new_msg = message.model_copy(
       update={"text": query, "entities": None}
   ).as_(bot)
   await dispatcher.propagate_event(
       "message", new_msg,
       bot=bot, dispatcher=dispatcher, impersonate_ref=ref,
   )
   ```

`bot` and `dispatcher` are injected by aiogram into handler data.

## Data flow

```
admin sends: /as 30000001 /me
  → PrincipalMiddleware: no impersonate_ref → principal = admin
  → cmd_as: admin ok; ref="30000001", query="/me"; target found
  → reply "👤 Showing as Ivan Ivanov:"
  → propagate_event(text="/me", impersonate_ref="30000001")
      → PrincipalMiddleware: real principal = admin + ref set
        → principal = student 30000001
      → cmd_me: renders student's own profile with STUDENT visibility
  → both messages land in the admin's chat
```

Free text (`/as 30000001 Ivanov`) re-feeds `"Ivanov"`, which is not a command,
so the NL fallback runs the search intent with the student as viewer.

## Safety / edge cases

- **Admin-only, enforced twice.** `cmd_as` checks up front; the middleware swap
  only fires when the real caller is an admin. The middleware check is the one
  that actually grants impersonation.
- **No privilege escalation.** The swap requires the real caller to already be an
  admin, so impersonating anyone (even another admin) grants nothing the human
  didn't already have.
- **`/sync` under admin-impersonation is not blocked.** If an admin impersonates
  another admin and sends `/sync`, it really runs. Accepted: the human is
  already an admin and could run `/sync` directly — no new capability. Flagged
  here so it is not a surprise. Impersonating a student and sending `/sync`
  yields the normal "Admins only." denial, which is the correct student view.
- **Nested `/as`.** `/as <ref1> /as <ref2> ...` re-feeds with the principal
  swapped to `ref1`'s target, which is not necessarily a student — if `ref1` is
  another admin, the inner `cmd_as` still passes the admin check and continues.
  Termination is guaranteed structurally, not by the admin check: each re-feed
  strips one `/as <ref>` prefix from the query text, so the text strictly
  shrinks and is bounded by Telegram's message length, ruling out unbounded
  recursion. There is no security impact either way, since the real caller is
  already an admin.
- **Bad input.** Missing args, empty query, or unknown ref produce friendly
  messages and no re-feed.

## Testing (matches existing mock-based style)

- `find_impersonation_target`: resolves by matriculation; falls back to
  telegram_id when `ref.isdigit()` and no matriculation match; prefers
  matriculation even when numeric; returns `None` when nothing matches.
- `PrincipalMiddleware`: swaps to target when real principal is admin and
  `impersonate_ref` is present; ignores `impersonate_ref` when the caller is a
  non-admin (principal stays the caller); unchanged when no `impersonate_ref`.
- `cmd_as`: denies non-admin and `None`; usage message on missing args / empty
  query; not-found message for unknown ref; on success sends the header and
  calls `dispatcher.propagate_event` (AsyncMock) with the correct `text` and
  `impersonate_ref` kwargs.
- Feature contract: `manifest` exposes the `as` command; `build_dispatcher`
  discovers the `impersonate` router.

## Out of scope

- Persisting/auditing impersonation events to the DB.
- A "stop impersonating" stateful session mode (each `/as` is one-shot).
- Impersonating by name/handle (only matriculation / telegram_id).

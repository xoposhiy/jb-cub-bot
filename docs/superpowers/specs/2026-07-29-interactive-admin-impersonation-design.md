# Interactive admin impersonation — design

**Date:** 2026-07-29
**Status:** Approved by request

## Goal

Make `/as <ref> /me`, `/privacy`, and `/edit` behave like the student's real
interactive screens. An admin may change the student's bot-owned profile values
and visibility settings through the same controls the student has.

This supersedes the earlier read-only `/as` decision for privacy and editing.

## Constraints and decisions

- `/as` stays one-shot; it must not silently turn later unrelated admin input
  into student input.
- Every impersonated button carries a canonical target reference in its
  `callback_data`. Middleware accepts it only when the real caller is an admin,
  resolves the target again, and injects the same impersonated principal.
- An edit-field tap copies the reference into that admin's FSM data. This is
  required for the following free-text value and `/cancel`, which have no
  callback payload. Clearing the FSM ends that continuation.
- All keyboards redrawn inside the flow retain the reference, including Back,
  Cancel and clear-confirmation buttons.

Rejected: a global or message-id in-memory impersonation map. It would either
leak into unrelated commands or disappear on restart and require shared mutable
state; carrying the context in buttons keeps it explicit and local.

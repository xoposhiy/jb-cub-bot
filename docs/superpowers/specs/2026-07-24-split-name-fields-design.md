# Design: Split `name` into `last_name` + `first_name`

Date: 2026-07-24

## Goal

The directory currently stores a person's identity as a single `User.name`
string, populated from a single "Full Name" sheet column. Real cohort sheets
now use two columns, **"Last name"** and **"First name"**, each of which may
contain several space-separated names (e.g. Spanish naming conventions:
`García Márquez` / `Gabriel José`). Replace the single field with two, and make
the whole pipeline — sheet mapping, sync, storage, search, and rendering — work
with the split.

## Decisions

- **Model**: split into two columns `last_name` and `first_name`; drop `name`.
- **No migration**: the schema is always created fresh from the model
  (`create_all`); there is no deployed database with real rows yet.
- **Display**: a single profile line `Name: {first_name} {last_name}`
  (first name first).
- **Helper**: a `full_name` property on the `User` model,
  `f"{first_name} {last_name}".strip()`, reused by render and handlers.
- **Legacy single-column support is dropped**: mappings must provide the two
  new fields. All real mappings are updated; the `cohort-2024.yaml` stub is
  deleted.

## Changes

### 1. Model (`src/jbcub_bot/core/models.py`)

Replace:

```python
name: Mapped[str] = mapped_column(String, default="")
```

with:

```python
last_name:  Mapped[str] = mapped_column(String, default="")
first_name: Mapped[str] = mapped_column(String, default="")
```

Add a property:

```python
@property
def full_name(self) -> str:
    return f"{self.first_name} {self.last_name}".strip()
```

### 2. Mappings (`mapping/`)

`sdt-2025-2028.yaml` and `rights.yaml` replace the `name: "Full Name"` line
with:

```yaml
last_name: "Last name"
first_name: "First name"
```

`mapping/cohort-2024.yaml` is **deleted** — it is a non-real stub, referenced
only by test fixtures, which are retargeted (see Tests).

### 3. Sheets sync (`src/jbcub_bot/core/sheets.py`)

`SHEET_OWNED` swaps `"name"` for `"last_name", "first_name"`. `normalize_rows`
already maps arbitrary field names by header, so it needs no change — it will
produce `last_name` / `first_name` keys per record. `upsert_users` iterates
`SHEET_OWNED`, so both new fields are persisted automatically.

### 4. Rendering (`src/jbcub_bot/features/directory/render.py`)

`_LABELS` and `_ORDER` keep a synthetic `"name"` key labelled `"Name"`.
`render_profile` composes the value from the two visible name fields rather
than a single field, emitting one `Name: {first} {last}` line. If both name
fields are empty/invisible, no Name line is emitted.

### 5. Visibility (`src/jbcub_bot/features/directory/visibility.py`)

`SUPER_MINIMUM` exposes `last_name` and `first_name` (both always visible to
any student/teacher/admin) in place of `name`. `visible_fields` sets
`fields["last_name"]` and `fields["first_name"]` from the target. Render reads
these two keys to build the combined Name line.

### 6. Search (`src/jbcub_bot/features/directory/search.py`)

`search_users` matches the query (case-insensitive `ilike`) against
`User.last_name` and `User.first_name` in addition to the existing
`handle_sheet` / `handle_observed`.

### 7. Handlers (`src/jbcub_bot/features/directory/handlers.py`)

The four `.name` usages (member list line, search-result line, "Linked as",
"Welcome back") switch to `user.full_name`.

### 8. Tests

Update to the split model and new columns:

- `test_models.py` — construct users with `first_name`/`last_name`; cover the
  `full_name` property.
- `test_sheets_normalize.py` — mapping/normalize examples use the two fields.
- `test_directory_sync.py` — retarget the fake Cohorts rows from
  `cohort-2024.yaml` to the real `sdt-2025-2028.yaml`; fake sheets use
  `"Last name"` / `"First name"` headers; `rights.yaml` fakes likewise.
- `test_directory_render.py` — assert the single combined `Name:` line.
- `test_directory_search.py` — search matches on either name field.
- `test_visibility.py` — assert `last_name`/`first_name` are super-minimum.
- Any other `User(name=...)` construction across the suite is updated to
  `first_name=`/`last_name=`.

## Out of scope

- No data migration (no live DB).
- No change to the two-line-internally / one-line-on-screen contract beyond
  what is described; visibility of name remains "always visible super-minimum".
- No renaming of the `"name"` synthetic key used purely for label/order.

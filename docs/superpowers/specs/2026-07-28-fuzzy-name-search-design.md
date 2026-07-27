# Fuzzy name search — design

**Date:** 2026-07-28
**Status:** Approved for planning

## Goal

`search_users` is four `ILIKE '%q%'` clauses, so a name matches only if the
query is a literal substring of it. Every roster name is stored in Latin
script, and people type Cyrillic, diacritics and whatever transliteration they
remember: `Ярослав` / `Iaroslav` / `Yaroslav`, `Пётр` / `Петр` / `Petr`,
`José` / `Jose'`. All of those must find their person.

The opposite requirement matters just as much: text that is not a name must
score low enough to be **declined**, because the intent chain is meant to grow
and the search intent (`pattern=".+"`) currently swallows every message.

## Matching — three stages, all pure

A new `features/directory/matching.py` holds `fold`, `skeleton` and `score` and
imports neither aiogram nor sqlalchemy, so it is testable as string→string.

**`fold`** — NFKD, drop combining marks, casefold, keep alnum. This alone
handles `José`/`Jose'` → `jose`, `ё` → `е`, `й` → `и`, `Hüseyn` → `huseyn`,
because the diacritic is a separate code point after decomposition.

**Transliteration** — a 37-entry Cyrillic→Latin dict in the same module.
Rejected `unidecode`: the output feeds our own collapse rules anyway, and the
table is small enough that owning it beats a dependency we would fight.

**`skeleton`** — ordered regex rules over the Latin form that collapse exactly
the ambiguity transliteration creates, so both spellings reduce to one code:
glides (`ya|ia|ja`→`a`, `yu|iu|ju`→`u`, `yo|io|jo`→`e`, `ye|ie|je`→`e`),
then `shch|sch|sh`→`S`, `ch`→`C`, `zh|j`→`J`, `kh|h`→`H`, `ph|f`→`F`,
`ts|tz|z`→`Z`, `x`→`KS`, `ck|q|k`→`K`, `w|v`→`V`, `c`→`S`/`K` by the next
letter, `y`→`I`, and finally repeats collapse. Measured:
`Ярослав = Iaroslav = Yaroslav → AROSLAV`, `Пётр = Petr = Pyotr = Piotr →
PETR`, `Алексей = Alexey = Aleksei → ALEKSEI`.

The `yo|io|jo` and `ye|ie|je` rules are **skipped word-initially** — otherwise
`Jose` becomes `ose`. Rule order is part of the contract; the rules live in one
readable tuple rather than scattered through the code.

A skeleton is deliberately coarse, so it is never the only signal.

## Scoring and thresholds

A person's tokens are the words of `first_name` and `last_name` plus both
handles — the roster has multi-word names on both sides (`Carlos Manuel
Alfonso Basabe`, `Ben Othman`). Each query word greedily takes its best
unclaimed token; the result is the mean. Running out of tokens scores 0, so a
query longer than the name never matches.

A word pair scores the max of: `fold` prefix (1.0), `SequenceMatcher` over
`fold`, skeleton prefix (0.95), `SequenceMatcher` over skeletons × 0.95 —
penalised because skeletons are the signal most likely to lie. `difflib`, not
`rapidfuzz`: 57 rows scanned in Python are free, and this stays dependency-free.

Numbers come from running the prototype over the real 57-row roster:

| | best | worst |
|---|---|---|
| queries that should match | 1.00 | 0.84 (`Кокеридзе` → Nika Kokheridze) |
| queries that should not | 0.76 (`Иванов`, nobody by that name) | 0.39 |

- **Accept at 0.80** — the gap between the two classes is wider than the
  tolerance either side needs.
- **Refuse queries under 3 letters** outright; no metric means anything on two.
- **Clear leader → profile**, when `best - second ≥ 0.05`. Otherwise a list of
  everyone within 0.15 of the leader and still above 0.80, capped at 20.
  Ties are real: `Михаил`,
  `Давид` and `Али` each score 0.95 against two different people.

Every threshold is a named constant in `matching.py`; tuning them must not mean
reading the scoring code.

## Declining, not answering

`Intent.handler` returns `bool`. `IntentRouter.dispatch` walks every intent
whose pattern matches, in registration order, and stops at the first that
returns `True`; `None` counts as handled so a future handler that forgets to
return cannot fail silently. Below-threshold search returns `False` **without
answering**, and a fallback intent registered last owns the `No one found.`
reply. Only one intent exists today, so the change is cheap now and impossible
later.

Rejected: a `can_handle` predicate on `Intent`. It moves the same scoring
in front of the handler and then has to compute it twice or cache it.

Also rejected: Postgres `pg_trgm` + `unaccent` (does nothing for
Cyrillic-against-Latin, and ties the feature to one database) and a
precomputed key column filled by `/sync` (57 rows; revisit two orders of
magnitude from now).

## Files

- **New:** `features/directory/matching.py`, `tests/test_matching.py`.
- **Changed:** `search.py` (`search_users` → `rank_users`, returning
  `(score, User)` sorted and cut at the threshold), `handlers.py`
  (`name_search` ranks, returns `bool`, applies the leader rule; new fallback
  intent), `core/intents.py` (bool contract, walk all matches),
  `directory/__init__.py`, `AGENTS.md`.

## Testing strategy

- **`test_matching.py`** — a skeleton table covering every spelling pair named
  here, `fold` invariants, and should-match / should-not-match pairs asserted
  against the threshold. This table is the guard against a new rule fixing one
  name and breaking three.
- **`test_directory_search.py`** — ranking order, the threshold, Cyrillic
  queries against the Latin roster, a query longer than the name.
- **`test_intents.py`** — `False` passes the turn to the next intent; the
  fallback answers last.
- **Integration** — a two-way tie renders the list, a clear leader renders the
  profile, and small talk reaches neither.

## Out of scope (YAGNI)

- Search over anything but names and handles.
- Extracting a name from a sentence (`кто такой Ярослав`) — a later intent's job.
- Non-Latin, non-Cyrillic scripts; per-language phonetics beyond the rules above.
- Persisted or indexed search keys, and any DB-side matching.

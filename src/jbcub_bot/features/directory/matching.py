"""Script-agnostic name matching.

Roster names are stored in Latin script, but people search in Cyrillic, with
diacritics, and in whichever transliteration they happen to remember. So a
word is never compared as typed: it is compared through two derived forms.

`fold` strips everything that is decoration -- diacritics, case, punctuation.
`skeleton` goes further and collapses the ambiguity transliteration itself
creates, so that every spelling of one name reduces to the same code:
Ярослав, Iaroslav and Yaroslav all become AROSLAV.

A skeleton is deliberately coarse, so it never decides a match alone -- it is
one signal among several in `word_score`, and a penalised one.
"""

import re
import unicodedata

# Below this a match is not reported at all: the search intent declines and the
# turn passes to whatever comes next. Measured over the roster, real matches
# score 0.84 and up while non-names stay at 0.76 and below.
ACCEPT = 0.80
# A leader at least this far ahead of the runner-up is shown as a profile.
LEAD = 0.05
# Everyone scoring within this of the leader is listed next to them.
SPREAD = 0.15
# No metric says anything useful about a one- or two-letter query.
MIN_QUERY_LEN = 3

CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ї": "i", "є": "e", "ґ": "g", "ў": "u",
}

# A leading glide is part of the name; a leading yo/ye is not -- collapsing it
# would eat the first letter of Latin names like Jose, so those two rules only
# fire after another character.
GLIDES = (
    (r"ya|ia|ja", "a"),
    (r"yu|iu|ju", "u"),
    (r"(?<=.)(?:yo|io|jo)", "e"),
    (r"(?<=.)(?:ye|ie|je)", "e"),
)

# Ordered: each rule may consume letters the next one would have matched, so
# the sequence is part of the contract. Uppercase output marks a finished
# phoneme -- later rules only ever match lowercase input.
RULES = (
    (r"shch|sch|sh", "S"),
    (r"tch|ch", "C"),
    (r"zh|j", "J"),
    (r"kh|h", "H"),
    (r"ph|f", "F"),
    (r"ts|tz|z", "Z"),
    (r"x", "KS"),
    (r"ck|q|k", "K"),
    (r"w|v", "V"),
    (r"c(?=[eiy])", "S"),
    (r"c", "K"),
    (r"y", "I"),
)


def fold(text: str) -> str:
    """Lowercase, diacritic-free, punctuation-free form of `text`.

    NFKD splits a decorated letter into a plain one plus its marks, so
    dropping the marks turns José into jose and ё into е for free.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in stripped if c.isalnum())


def latinize(text: str) -> str:
    return "".join(CYRILLIC.get(c, c) for c in fold(text))


def skeleton(text: str) -> str:
    """Coarse spelling-independent code for `text`."""
    word = latinize(text)
    for pattern, replacement in GLIDES:
        word = re.sub(pattern, replacement, word)
    for pattern, replacement in RULES:
        word = re.sub(pattern, replacement, word)
    return re.sub(r"(.)\1+", r"\1", word.upper())

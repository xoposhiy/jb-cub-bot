"""A `register` that declares nothing at all -- not even `describe`. The
feature still exists (its slot is created by the api it was handed), so
`validate()` can name it.
"""


def register(bot) -> None:
    pass

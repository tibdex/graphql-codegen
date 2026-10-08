from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def error_note(note: str, /) -> Iterator[None]:
    """Add *note* to an error raised inside, so that a function need not know where its caller found what it checks."""
    try:
        yield
    except Exception as error:
        error.add_note(note)
        raise

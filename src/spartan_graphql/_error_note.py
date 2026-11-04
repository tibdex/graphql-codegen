from collections.abc import Generator
from contextlib import contextmanager


@contextmanager
def error_note(note: str, /) -> Generator[None]:
    try:
        yield
    except Exception as error:
        error.add_note(note)
        raise

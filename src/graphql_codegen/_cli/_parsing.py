"""Parse the values of a config file, noting where each error is."""

from collections.abc import Callable, Mapping
from typing import cast

from graphql_codegen._note import error_note


def check_all_read(unread: dict[str, object], /) -> None:
    if unread:
        raise ValueError(
            f"Expected no other key, but got {', '.join(f'`{key}`' for key in sorted(unread))}."
        )


def parse_dict(value: object, /) -> dict[str, object]:
    """Return a shallow-copy of *value*."""
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"Expected a mapping with string keys, but got `{value!r}`.")

    return dict(cast(Mapping[str, object], value))


def required[T](
    unread: dict[str, object], key: str, parse: Callable[[object], T], /
) -> T:
    value = optional(unread, key, parse, default=None)

    if value is None:
        raise ValueError(f"Expected `{key}` to be set.")

    return value


def optional[T](
    unread: dict[str, object], key: str, parse: Callable[[object], T], /, *, default: T
) -> T:
    value = unread.pop(key, None)
    return default if value is None else parse_at(value, key=key, parse=parse)


def parse_at[T](value: object, /, *, key: str, parse: Callable[[object], T]) -> T:
    with error_note(f"In `{key}`."):
        return parse(value)


def parse_strings(value: object, /) -> list[str]:
    if isinstance(value, str):
        return [value]

    if not isinstance(value, list):
        raise TypeError(f"Expected a string or a list of strings, but got `{value!r}`.")

    strings: list[str] = []

    for index, item in enumerate(value):
        with error_note(f"At index {index}."):
            strings.append(parse_string(item))

    return strings


def parse_string(value: object, /) -> str:
    if not isinstance(value, str):
        raise TypeError(f"Expected a string, but got `{value!r}`.")

    return value

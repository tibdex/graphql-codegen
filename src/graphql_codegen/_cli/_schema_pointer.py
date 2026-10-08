from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast, final
from urllib.parse import urlsplit

from graphql_codegen._cli._parsing import (
    check_all_read,
    optional,
    parse_at,
    parse_dict,
    parse_string,
    required,
)
from graphql_codegen._note import error_note


@final
@dataclass(frozen=True, kw_only=True)
class Globs:
    patterns: tuple[str, ...]


@final
@dataclass(frozen=True, kw_only=True)
class IntrospectionResult:
    path: PurePosixPath


@final
@dataclass(frozen=True, kw_only=True)
class Endpoint:
    url: str
    headers: Mapping[str, str]


type SchemaPointer = Globs | IntrospectionResult | Endpoint
"""Where a project's schema comes from, in one of the forms https://the-guild.dev/graphql/config/docs/user/schema lists."""


def parse_schema_pointer(value: object, /) -> SchemaPointer:
    match value:
        case [item]:
            with error_note("At index 0."):
                return _parse_single_pointer(item)
        case list():
            patterns: list[str] = []

            for index, item in enumerate(value):
                with error_note(f"At index {index}."):
                    pointer = _parse_single_pointer(item)

                    if not isinstance(pointer, Globs):
                        raise TypeError(
                            f"Expected a glob of SDL files, since the list holds more than one item, but got `{item}`."
                        )

                # A single item is one glob.
                (pattern,) = pointer.patterns
                patterns.append(pattern)

            return Globs(patterns=tuple(patterns))
        case _:
            return _parse_single_pointer(value)


def _parse_single_pointer(value: object, /) -> SchemaPointer:
    if isinstance(value, str):
        if (url := _parse_url(value)) is not None:
            return Endpoint(url=url, headers={})

        if (path := PurePosixPath(value)).suffix == ".json":
            return IntrospectionResult(path=path)

        return Globs(patterns=(value,))

    if isinstance(value, Mapping) and len(value) == 1:
        ((key, options),) = cast(Mapping[object, object], value).items()

        if isinstance(key, str) and (url := _parse_url(key)) is not None:
            return Endpoint(
                url=url,
                headers={}
                if options is None
                else parse_at(options, key=key, parse=_parse_endpoint_headers),
            )

    raise TypeError(
        f"Expected a glob, a `.json` path, or a URL (possibly mapped to its options), but got `{value!r}`."
    )


def _parse_url(value: str, /) -> str | None:
    split_result = urlsplit(value)

    return (
        split_result.geturl()
        # `urlsplit()` lowercases the scheme, which `geturl()` keeps.
        if split_result.scheme in {"http", "https"}
        else None
    )


def _parse_endpoint_headers(value: object, /) -> dict[str, str]:
    unread = parse_dict(value)
    headers = optional(unread, "headers", parse_dict, default={})
    check_all_read(unread)
    return {name: required(headers, name, parse_string) for name in list(headers)}

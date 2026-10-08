"""Read a project's GraphQL: its documents' files, and its schema, whatever form it comes in."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import assert_never, cast

from graphql import IntrospectionQuery, Source, build_client_schema, print_schema

from graphql_codegen._cli._introspection import fetch_sdl
from graphql_codegen._cli._schema_pointer import (
    Endpoint,
    Globs,
    IntrospectionResult,
    SchemaPointer,
)
from graphql_codegen._note import error_note


def read_sources(globs: Sequence[str], /, *, directory: Path) -> list[Source]:
    paths: set[Path] = set()

    for glob in globs:
        matched = {path for path in directory.glob(glob) if path.is_file()}

        if not matched:
            # Also catches https://the-guild.dev/graphql/config's braces and negation, which `pathlib` reads literally: a negation would otherwise leave in what it means to leave out.
            raise ValueError(f"Expected `{glob}` to match at least one file.")

        paths |= matched

    sources: list[Source] = []

    # Sorted, so that the package does not depend on the file system's order.
    for path in sorted(paths):
        text = path.read_text(encoding="utf-8")
        # Named by their POSIX paths, in which a module next to a document is placed.
        sources.append(Source(text, path.as_posix()))

    return sources


def read_schema(pointer: SchemaPointer, /, *, directory: Path) -> list[Source]:
    match pointer:
        case Globs(patterns=patterns):
            return read_sources(patterns, directory=directory)
        case IntrospectionResult(path=path):
            file = directory / path

            with error_note(f"In `{file.as_posix()}`."):
                text = file.read_text(encoding="utf-8")
                return [Source(_introspection_sdl(json.loads(text)), file.as_posix())]
        case Endpoint(url=url, headers=headers):
            with error_note(f"In `{url}`."):
                sdl = fetch_sdl(url, headers=headers)
                return [Source(sdl, url)]
        case _ as never:
            assert_never(never)


def _introspection_sdl(result: object, /) -> str:
    if isinstance(result, dict) and "data" in result:
        result = result["data"]

    if not isinstance(result, dict) or "__schema" not in result:
        raise ValueError("Expected an introspection result, holding `__schema`.")

    return print_schema(build_client_schema(cast(IntrospectionQuery, result)))

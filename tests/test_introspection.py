from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Final

from graphql import (
    GraphQLSchema,
    Source,
    build_schema,
    get_introspection_query,
    graphql_sync,
    parse,
    print_schema,
)

import graphql_codegen
from graphql_codegen import Config, DocumentSiblingModule, PackageLocation, generate
from graphql_codegen._cli._introspection import fetch_sdl
from tests._graphql_server import GraphqlHandler

_PACKAGE_DIRECTORY: Final = Path(graphql_codegen.__file__).parent
_DOCUMENT: Final = _PACKAGE_DIRECTORY / "_cli" / "_introspection.graphql"
_MODULE: Final = _DOCUMENT.with_name("_introspection_graphql.py")


def _generated_module() -> bytes:
    files = generate(
        document=parse(
            Source(_DOCUMENT.read_text(encoding="utf-8"), _DOCUMENT.as_posix())
        ),
        # Every schema answers introspection.
        schema=build_schema("type Query { _: Boolean }"),
        config=Config(
            document_sibling_module=DocumentSiblingModule(
                name="{document}_graphql",
                package_location=PackageLocation(
                    module_root=PurePosixPath(_PACKAGE_DIRECTORY.parent.as_posix()),
                    package=graphql_codegen.__name__,
                ),
            ),
            non_null_directive_name="nonNull",
        ),
    )
    return files[PurePosixPath(_MODULE.relative_to(_PACKAGE_DIRECTORY).as_posix())]


def test_the_introspection_document_s_module_is_what_the_library_generates() -> None:
    # The rest of the generated package is the library itself.
    assert _MODULE.read_bytes() == _generated_module()


def test_an_endpoint_is_introspected_with_its_headers_for_all_it_supports(
    graphql_server: Callable[[GraphqlHandler], str], bookshop_schema: GraphQLSchema
) -> None:
    queries: list[str] = []

    def handle(query: str, /, *, headers: Mapping[str, str]) -> object:
        assert headers["Authorization"] == "Bearer t"
        assert headers["User-Agent"].startswith("graphql-codegen/")
        queries.append(query)
        return graphql_sync(bookshop_schema, query).formatted

    sdl = fetch_sdl(graphql_server(handle), headers={"Authorization": "Bearer t"})

    assert print_schema(build_schema(sdl)) == print_schema(bookshop_schema)
    assert queries[1] == get_introspection_query(
        specified_by_url=True,
        directive_is_repeatable=True,
        schema_description=True,
        input_value_deprecation=True,
        one_of=True,
    )


def test_an_older_endpoint_is_asked_only_what_it_knows(
    graphql_server: Callable[[GraphqlHandler], str], bookshop_schema: GraphQLSchema
) -> None:
    """As a server predating every introspection field added since the October 2016 edition of the spec."""
    later = {"description", "isDeprecated", "isOneOf", "isRepeatable", "specifiedByURL"}
    queries: list[str] = []

    def handle(query: str, /, *, headers: Mapping[str, str]) -> object:
        queries.append(query)
        result = graphql_sync(bookshop_schema, query).formatted

        if len(queries) == 1:
            assert result["data"] is not None
            known = {
                field["name"]
                for type_ in result["data"].values()
                for field in type_["fields"]
            }
            # Names the server really answers, rather than misspelled ones that would strip nothing.
            assert later <= known, later - known

            for type_ in result["data"].values():
                type_["fields"] = [
                    field for field in type_["fields"] if field["name"] not in later
                ]

        return result

    fetch_sdl(graphql_server(handle), headers={})

    assert queries[1] == get_introspection_query()


if __name__ == "__main__":
    # `uv run python -m tests.test_introspection` regenerates it.
    _MODULE.write_bytes(_generated_module())

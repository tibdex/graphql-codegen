import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath

import pytest
from graphql import (
    GraphQLSchema,
    build_schema,
    graphql_sync,
    introspection_from_schema,
    print_schema,
)

from graphql_codegen._cli._schema_pointer import Endpoint, IntrospectionResult
from graphql_codegen._cli._source import read_schema, read_sources
from graphql_codegen.runtime import RequestError
from tests._bookshop import copy_bookshop
from tests._graphql_server import GraphqlHandler


def test_sources_are_read_in_sorted_order_and_named_by_their_paths(
    tmp_path: Path,
) -> None:
    copy_bookshop(tmp_path)

    assert [
        source.name
        for source in read_sources(
            ["get_order.graphql", "*.graphql"], directory=tmp_path
        )
    ] == [
        (tmp_path / "app.graphql").as_posix(),
        (tmp_path / "get_order.graphql").as_posix(),
    ]


@pytest.mark.parametrize(
    ("glob", "error", "message"),
    [
        pytest.param(
            "missing/*.graphql",
            ValueError,
            "Expected `missing/*.graphql` to match at least one file.",
            id="matching nothing",
        ),
        pytest.param(
            "/*.graphql",
            NotImplementedError,
            "Non-relative patterns are unsupported",
            id="absolute, which pathlib rejects",
        ),
    ],
)
def test_each_glob_must_match_a_file(
    glob: str, error: type[Exception], message: str, tmp_path: Path
) -> None:
    copy_bookshop(tmp_path)

    with pytest.raises(error, match=f"^{re.escape(message)}$"):
        read_sources(["*.graphql", glob], directory=tmp_path)


@pytest.mark.parametrize(
    "wrap",
    [
        pytest.param(lambda result: result, id="bare"),
        pytest.param(lambda result: {"data": result}, id="as a response's data"),
    ],
)
def test_an_introspection_result_is_read_from_its_file(
    wrap: Callable[[object], object], bookshop_schema: GraphQLSchema, tmp_path: Path
) -> None:
    path = tmp_path / "schema.json"
    path.write_text(
        json.dumps(wrap(introspection_from_schema(bookshop_schema))), encoding="utf-8"
    )

    (source,) = read_schema(
        IntrospectionResult(path=PurePosixPath("schema.json")), directory=tmp_path
    )

    assert source.name == path.as_posix()
    assert print_schema(build_schema(source.body)) == print_schema(bookshop_schema)


def test_an_introspection_result_s_error_names_its_file(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    path.write_text('{"errors": []}', encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="^"
        + re.escape(
            f"Expected an introspection result, holding `__schema`.\nIn `{path.as_posix()}`."
        )
        + "$",
    ):
        read_schema(
            IntrospectionResult(path=PurePosixPath("schema.json")), directory=tmp_path
        )


def test_an_endpoint_s_schema_is_named_by_its_url(
    graphql_server: Callable[[GraphqlHandler], str],
    bookshop_schema: GraphQLSchema,
    tmp_path: Path,
) -> None:
    def handle(query: str, /, *, headers: Mapping[str, str]) -> object:
        return graphql_sync(bookshop_schema, query).formatted

    url = graphql_server(handle)

    (source,) = read_schema(Endpoint(url=url, headers={}), directory=tmp_path)

    assert source.name == url


def test_an_endpoint_s_error_names_its_url(
    graphql_server: Callable[[GraphqlHandler], str], tmp_path: Path
) -> None:
    url = graphql_server(
        lambda _query, /, *, headers: {"errors": [{"message": "Bad credentials."}]}
    )

    with pytest.raises(RequestError, match=f"\\nIn `{re.escape(url)}`\\.$"):
        read_schema(Endpoint(url=url, headers={}), directory=tmp_path)

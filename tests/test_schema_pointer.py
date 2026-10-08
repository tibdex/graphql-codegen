import re
from pathlib import PurePosixPath

import pytest

from graphql_codegen._cli._schema_pointer import (
    Endpoint,
    Globs,
    IntrospectionResult,
    parse_schema_pointer,
)


@pytest.mark.parametrize(
    ("schema", "pointer"),
    [
        pytest.param(
            "schema.graphqls", Globs(patterns=("schema.graphqls",)), id="a glob"
        ),
        pytest.param(
            ["a.graphqls", "b/*.graphqls"],
            Globs(patterns=("a.graphqls", "b/*.graphqls")),
            id="globs",
        ),
        pytest.param(
            "schema.json",
            IntrospectionResult(path=PurePosixPath("schema.json")),
            id="a JSON file",
        ),
        pytest.param(
            "https://a.example/graphql",
            Endpoint(url="https://a.example/graphql", headers={}),
            id="a URL",
        ),
        pytest.param(
            "HTTPS://a.example/graphql",
            Endpoint(url="https://a.example/graphql", headers={}),
            id="a URL whose scheme is uppercase, since schemes ignore case",
        ),
        pytest.param(
            [{"https://a.example/graphql": {"headers": {"Authorization": "Bearer t"}}}],
            Endpoint(
                url="https://a.example/graphql",
                headers={"Authorization": "Bearer t"},
            ),
            id="a URL mapped to its headers, in a list of one as graphql-config writes it",
        ),
        pytest.param(
            {"https://a.example/graphql": None},
            Endpoint(url="https://a.example/graphql", headers={}),
            id="a URL mapped to no options",
        ),
    ],
)
def test_a_schema_comes_from_one_of_graphql_config_s_forms(
    schema: object, pointer: object
) -> None:
    assert parse_schema_pointer(schema) == pointer


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        pytest.param(
            {"schema.graphqls": None},
            "Expected a glob, a `.json` path, or a URL (possibly mapped to its options), but got `{'schema.graphqls': None}`.",
            id="options for a file",
        ),
        pytest.param(
            3,
            "Expected a glob, a `.json` path, or a URL (possibly mapped to its options), but got `3`.",
            id="neither a string nor a mapping",
        ),
        pytest.param(
            [{"https://a.example/graphql": {"method": "GET"}}],
            "Expected no other key, but got `method`.\nIn `https://a.example/graphql`.\nAt index 0.",
            id="an option other than headers",
        ),
        pytest.param(
            ["schema.graphqls", "https://a.example/graphql"],
            "Expected a glob of SDL files, since the list holds more than one item, but got `https://a.example/graphql`.\nAt index 1.",
            id="a URL among globs",
        ),
        pytest.param(
            ["schema.graphqls", "schema.json"],
            "Expected a glob of SDL files, since the list holds more than one item, but got `schema.json`.\nAt index 1.",
            id="a JSON file among globs",
        ),
    ],
)
def test_a_malformed_schema_pointer_is_reported(schema: object, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=f"^{re.escape(message)}$"):
        parse_schema_pointer(schema)

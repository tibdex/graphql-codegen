import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Final

import pytest
from graphql import Source

from graphql_codegen import Config, PackageLocation
from graphql_codegen._cli._graphql_config import (
    _get_loader,
    _interpolate,
    _parse_graphql_config,
    _parse_project,
    _Project,
    read_graphql_config,
)
from graphql_codegen._cli._schema_pointer import Globs
from tests._bookshop import copy_bookshop

_EXTENSION: Final = {"moduleRoot": ".", "package": "client"}
_PROJECT: Final = {
    "schema": "schema.graphqls",
    "documents": "*.graphql",
    "extensions": {"pythonCodegen": _EXTENSION},
}
_IN_EXTENSION: Final = "\nIn `extensions.pythonCodegen`.\nIn project `default`."


@pytest.mark.parametrize(
    ("name", "content"),
    [
        pytest.param("graphql.config.json", json.dumps(_PROJECT), id="json"),
        pytest.param(
            "graphql.config.toml",
            'schema = "schema.graphqls"\ndocuments = "*.graphql"\n\n[extensions.pythonCodegen]\nmoduleRoot = "."\npackage = "client"\n',
            id="toml",
        ),
        pytest.param(
            ".graphqlrc", json.dumps(_PROJECT), id="extensionless, read as YAML"
        ),
    ],
)
def test_every_format_python_can_read_is_read(
    name: str, content: str, tmp_path: Path
) -> None:
    """Each project's arguments are keyed by its package's directory, which its module root, relative to the config file's, is re-rooted at."""
    copy_bookshop(tmp_path)
    (tmp_path / name).write_text(content, encoding="utf-8")

    (directory,) = read_graphql_config(tmp_path / name)

    assert directory == tmp_path / "client"


def test_a_yaml_config_without_the_extra_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A `None` entry makes the import fail, as when PyYAML is not installed.
    monkeypatch.setitem(sys.modules, "yaml", None)

    with pytest.raises(ImportError, match=re.escape("install `graphql-codegen[yaml]`")):
        _get_loader(Path("graphql.config.yml"))


@pytest.mark.parametrize(
    ("name", "message"),
    [
        pytest.param(
            "graphql.config.ini", "Cannot tell the format of", id="unknown format"
        ),
        pytest.param(
            "graphql.config.ts", "only JavaScript can evaluate", id="javascript"
        ),
    ],
)
def test_a_config_python_cannot_read_is_rejected(name: str, message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        _get_loader(Path(name))


def test_every_project_with_the_extension_is_read() -> None:
    projects = {
        "shop": {
            **_PROJECT,
            "extensions": {"pythonCodegen": {**_EXTENSION, "package": "shop"}},
        },
        "back_office": {
            **_PROJECT,
            "extensions": {"pythonCodegen": {**_EXTENSION, "package": "back_office"}},
        },
        # For editors only.
        "server_tests": {"schema": "schema.graphqls", "documents": "*.graphql"},
    }

    assert {
        project.name
        for project in _parse_graphql_config(
            {"projects": projects}, directory=PurePosixPath()
        ).values()
    } == {"shop", "back_office"}


@pytest.mark.parametrize(
    "extension",
    [
        pytest.param(
            {
                **_EXTENSION,
                "documentSiblingModule": None,
                "injectorNames": None,
                "nonNullDirectiveName": None,
                "scalars": None,
                "structInterfaceName": None,
            },
            id="null, like left out",
        ),
    ],
)
def test_every_config_key_but_where_the_package_goes_is_optional(
    extension: dict[str, object],
) -> None:
    ((project),) = _parse_graphql_config(
        {**_PROJECT, "extensions": {"pythonCodegen": extension}},
        directory=PurePosixPath(),
    ).values()

    assert project.config == Config()


@pytest.mark.parametrize(
    ("config", "message"),
    [
        pytest.param(
            [], "Expected a mapping with string keys, but got `[]`.", id="not a mapping"
        ),
        pytest.param(
            {"schema": "schema.graphqls", "documents": "*.graphql"},
            "Expected a project with a `pythonCodegen` extension.",
            id="no extension",
        ),
        pytest.param(
            {"projects": dict.fromkeys(("shop", "back_office"), _PROJECT)},
            "Expected `shop` and `back_office` to generate into directories of their own, but both generate into `client`.",
            id="two projects into one directory, which would replace each other's",
        ),
        pytest.param(
            {"documents": "*.graphql", "extensions": {"pythonCodegen": _EXTENSION}},
            "Expected `schema` to be set.\nIn project `default`.",
            id="no schema",
        ),
    ],
)
def test_a_config_without_a_project_to_generate_fails(
    config: object, message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=f"^{re.escape(message)}$"):
        _parse_graphql_config(config, directory=PurePosixPath())


@pytest.mark.parametrize(
    ("extension", "message"),
    [
        pytest.param(
            {**_EXTENSION, "injectorName": ["idempotencyKey"]},
            f"Expected no other key, but got `injectorName`.{_IN_EXTENSION}",
            id="unknown key",
        ),
        pytest.param(
            {"moduleRoot": 3, "package": "client"},
            f"Expected a string, but got `3`.\nIn `moduleRoot`.{_IN_EXTENSION}",
            id="a module root that is not a string",
        ),
        pytest.param(
            {**_EXTENSION, "injectorNames": ["idempotencyKey", 3]},
            f"Expected a string, but got `3`.\nAt index 1.\nIn `injectorNames`.{_IN_EXTENSION}",
            id="a list holding something else than a string, located by its index",
        ),
        pytest.param(
            {**_EXTENSION, "injectorNames": 3},
            f"Expected a string or a list of strings, but got `3`.\nIn `injectorNames`.{_IN_EXTENSION}",
            id="neither a string nor a list",
        ),
        pytest.param(
            {**_EXTENSION, "documentSiblingModule": "_gql"},
            f"Expected the module's name to be an identifier holding `{{document}}` once, such as `_{{document}}_gql`, but got `_gql`.\nIn `documentSiblingModule`.{_IN_EXTENSION}",
            id="what the config checks, located by notes",
        ),
        pytest.param(
            {"moduleRoot": ".", "package": "my-client"},
            f"Expected the package to be a dotted name, such as `my_app._graphql`, but got `my-client`.{_IN_EXTENSION}",
            id="what the package location checks",
        ),
        pytest.param(
            {
                **_EXTENSION,
                "scalars": {
                    "Money": {
                        "type": "decimal.Decimal",
                        "codec": {"decode": ".x", "encode": "a.b"},
                    }
                },
            },
            f"Expected an absolute dotted name, such as `datetime.datetime`, a dotted name relative to the package's directory, such as `..scalars.decode`, or a builtin, such as `int`, but got `.x`.\nIn `decode`.\nIn `codec`.\nIn `Money`.\nIn `scalars`.{_IN_EXTENSION}",
            id="what a scalar checks, located by one note per level",
        ),
        pytest.param(
            {
                **_EXTENSION,
                "scalars": {
                    "Money": {"type": "int", "codec": {"decode": "my_app.decode"}}
                },
            },
            f"Expected `encode` to be set.\nIn `codec`.\nIn `Money`.\nIn `scalars`.{_IN_EXTENSION}",
            id="a codec decoding without encoding",
        ),
        pytest.param(
            {**_EXTENSION, "scalars": {"Money": {"type": 3}}},
            f"Expected a string, but got `3`.\nIn `type`.\nIn `Money`.\nIn `scalars`.{_IN_EXTENSION}",
            id="a scalar holding something else than strings",
        ),
    ],
)
def test_a_malformed_extension_is_reported_where_it_is(
    extension: object, message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=f"^{re.escape(message)}$"):
        _parse_graphql_config(
            {**_PROJECT, "extensions": {"pythonCodegen": extension}},
            directory=PurePosixPath(),
        )


def test_an_invalid_schema_is_reported() -> None:
    project = _Project(
        name="default",
        schema=Globs(patterns=()),
        documents=[],
        package_location=PackageLocation(module_root=PurePosixPath(), package="client"),
        config=Config(),
    )

    with pytest.raises(
        ValueError, match=re.escape("Cannot build the schema: Unknown type 'Missing'.")
    ):
        _parse_project(
            project,
            schema=[Source("type Query { book: Missing }")],
            documents=[],
        )


@pytest.mark.parametrize(
    ("text", "environment", "interpolated"),
    [
        pytest.param(
            "schema: ${SCHEMA}",
            {"SCHEMA": "a.graphqls"},
            "schema: a.graphqls",
            id="set",
        ),
        pytest.param(
            "schema: ${SCHEMA:a.graphqls}",
            {"SCHEMA": "b.graphqls"},
            "schema: b.graphqls",
            id="set, over its default",
        ),
        pytest.param(
            "schema: ${SCHEMA:a.graphqls}", {}, "schema: a.graphqls", id="defaulted"
        ),
        pytest.param(
            'schema: ${SCHEMA:"http://localhost:4000/graphql"}',
            {},
            "schema: http://localhost:4000/graphql",
            id="defaulted to a quoted value, which may hold a colon",
        ),
        pytest.param(
            "schema: ${SCHEMA:http://localhost:4000/graphql}",
            {},
            "schema: http://localhost:4000/graphql",
            id="or to an unquoted one holding colons",
        ),
        pytest.param(
            "schema: ${SCHEMA:'http://localhost:4000/graphql'}",
            {},
            "schema: http://localhost:4000/graphql",
            id="or in single quotes",
        ),
        pytest.param(
            "schema: ${SCHEMA: a.graphqls }", {}, "schema: a.graphqls", id="trimmed"
        ),
        pytest.param(
            "schema: ${SCHEMA:a.graphqls}",
            {"SCHEMA": ""},
            "schema: a.graphqls",
            id="set to nothing, which falls back to its default",
        ),
        pytest.param(
            "schema: ${SCHEMA:}",
            {},
            "schema: ${SCHEMA:}",
            id="with an empty default, which is no reference",
        ),
        pytest.param(
            "schema: ${schema}",
            {"schema": "a.graphqls"},
            "schema: a.graphqls",
            id="in lowercase",
        ),
    ],
)
def test_an_environment_variable_is_replaced_before_parsing(
    text: str, environment: Mapping[str, str], interpolated: str
) -> None:
    """Including graphql-config's own cases: https://github.com/graphql-hive/graphql-config/blob/3091fcc7259c2feeb9866994a6c3ca4fc6ddb7a0/test/config.spec.ts#L173-L202."""
    assert _interpolate(text, environment=environment) == interpolated


def test_an_environment_variable_without_a_value_or_default_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="^"
        + re.escape(
            "Expected the environment variable `SCHEMA` to have a value, since `${SCHEMA}` has no default."
        )
        + "$",
    ):
        # Rather than writing `undefined`, as graphql-config does.
        _interpolate("schema: ${SCHEMA}", environment={})

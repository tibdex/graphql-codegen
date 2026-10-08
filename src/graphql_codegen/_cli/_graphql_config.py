import json
import os
import posixpath
import re
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Final, final

from graphql import (
    DocumentNode,
    GraphQLError,
    Source,
    build_ast_schema,
    parse,
)

from graphql_codegen._cli._parsing import (
    check_all_read,
    optional,
    parse_at,
    parse_dict,
    parse_string,
    parse_strings,
    required,
)
from graphql_codegen._cli._schema_pointer import SchemaPointer, parse_schema_pointer
from graphql_codegen._cli._source import read_schema, read_sources
from graphql_codegen._metadata import DISTRIBUTION_NAME, METADATA
from graphql_codegen._note import error_note
from graphql_codegen.config import Config
from graphql_codegen.document_sibling_module import DocumentSiblingModule
from graphql_codegen.generate import _Params
from graphql_codegen.package_location import PackageLocation
from graphql_codegen.scalar import Codec, Scalar

_EXTENSION_NAME: Final = "pythonCodegen"

# As https://github.com/kamilkisiela/string-env-interpolation/blob/94c441088f2ba62c5f0a2a31b8b9728242184360/src/index.ts#L16, which graphql-config calls at https://github.com/graphql-hive/graphql-config/blob/3091fcc7259c2feeb9866994a6c3ca4fc6ddb7a0/src/helpers/cosmiconfig.ts#L24.
_ENVIRONMENT_VARIABLE: Final = re.compile(
    r"\$\{(?P<name>[A-Z0-9_]+)(?::(?P<default>[^}]+))?\}", re.IGNORECASE
)
_QUOTED: Final = re.compile(r"\"([^\"]+)\"|'([^']+)'")


def read_graphql_config(path: Path, /) -> dict[Path, _Params]:
    """Return the generation arguments for each project of the graphql-config file at *path* by the directory its package goes to."""
    load = _get_loader(path)
    text = path.read_text(encoding="utf-8")
    config = load(_interpolate(text, environment=os.environ))

    return {
        directory: _read_project(project, directory=path.parent)
        for directory, project in _parse_graphql_config(
            config, directory=PurePosixPath(path.parent.as_posix())
        ).items()
    }


def _interpolate(text: str, /, *, environment: Mapping[str, str]) -> str:
    def replacement(match: re.Match[str], /) -> str:
        name = match["name"]

        # An empty value falls back to the default too.
        if value := environment.get(name):
            return value

        if isinstance(default := match["default"], str):
            stripped = default.strip()
            quoted = _QUOTED.fullmatch(stripped)
            # One holding a colon may be quoted, in single or double quotes.
            return (
                stripped
                if quoted is None
                else next(group for group in quoted.groups() if isinstance(group, str))
            )

        raise ValueError(
            f"Expected the environment variable `{name}` to have a value, since `{match[0]}` has no default."
        )

    return _ENVIRONMENT_VARIABLE.sub(replacement, text)


def _get_loader(config_path: Path, /) -> Callable[[str], object]:
    match config_path.suffix:
        case ".json":
            return json.loads
        case ".toml":
            return tomllib.loads
        case ".yaml" | ".yml" | "":
            # `.graphqlrc` may be YAML or JSON, which YAML reads too.
            try:
                import yaml  # noqa: PLC0415
            except ImportError as error:
                raise ImportError(
                    f"`{config_path}` is YAML: install `{DISTRIBUTION_NAME}[{_yaml_extra_name()}]` to read it."
                ) from error

            return yaml.safe_load
        case ".js" | ".cjs" | ".mjs" | ".ts" | ".cts" | ".mts":
            raise ValueError(
                f"`{config_path}` is JavaScript or TypeScript, which only JavaScript can evaluate: write it in YAML, JSON, or TOML."
            )
        case suffix:
            raise ValueError(
                f"Cannot tell the format of `{config_path}` from `{suffix}`: expected YAML, JSON, or TOML."
            )


@cache
def _yaml_extra_name() -> str:
    name = "yaml"
    assert name in METADATA.get_all("Provides-Extra", ()), (
        f"Expected `pyproject.toml` to declare the `{name}` extra."
    )
    return name


@final
@dataclass(frozen=True, kw_only=True)
class _Project:
    name: str
    schema: SchemaPointer
    documents: Sequence[str]
    package_location: PackageLocation
    config: Config


def _read_project(project: _Project, /, *, directory: Path) -> _Params:
    with error_note(f"In project `{project.name}`."):
        return _parse_project(
            project,
            schema=read_schema(project.schema, directory=directory),
            documents=read_sources(project.documents, directory=directory),
        )


def _parse_project(
    project: _Project,
    /,
    *,
    schema: Sequence[Source],
    documents: Sequence[Source],
) -> _Params:
    try:
        built_schema = build_ast_schema(_parse_sources(schema))
    except (GraphQLError, TypeError) as error:
        # `build_ast_schema()` raises a `TypeError` for an invalid schema.
        raise ValueError(f"Cannot build the schema: {error}") from error

    return _Params(
        document=_parse_sources(documents),
        schema=built_schema,
        config=project.config,
    )


def _parse_sources(sources: Sequence[Source], /) -> DocumentNode:
    """Parse *sources* into one document, since a fragment defined in one file may be spread in another, and a schema's types spread over several files."""
    return DocumentNode(
        definitions=tuple(
            definition for source in sources for definition in parse(source).definitions
        )
    )


def _parse_graphql_config(
    config: object, /, *, directory: PurePosixPath
) -> dict[Path, _Project]:
    """The projects of *config*, the graphql-config file in *directory*, by the directory each package goes to."""
    root = parse_dict(config)
    entries = (
        required(root, "projects", parse_dict)
        if "projects" in root
        else {"default": root}
    )
    projects: dict[Path, _Project] = {}

    for name, entry in entries.items():
        with error_note(f"In project `{name}`."):
            project = _project(entry, name=name, directory=directory)

        if project is None:
            continue

        # Derived from both, which needs no IO, unlike finding where packages start from a directory.

        package_directory = Path(project.package_location._package_directory)

        if (other := projects.get(package_directory)) is not None:
            raise ValueError(
                f"Expected `{other.name}` and `{name}` to generate into directories of their own, but both generate into `{package_directory}`."
            )

        projects[package_directory] = project

    if not projects:
        raise ValueError(f"Expected a project with a `{_EXTENSION_NAME}` extension.")

    return projects


def _project(
    value: object, /, *, name: str, directory: PurePosixPath
) -> _Project | None:
    entry = parse_dict(value)
    extensions = optional(entry, "extensions", parse_dict, default={})

    if (extension := extensions.get(_EXTENSION_NAME)) is None:
        return None

    schema = required(entry, "schema", parse_schema_pointer)
    documents = required(entry, "documents", parse_strings)
    package_location, config = parse_at(
        extension,
        key=f"extensions.{_EXTENSION_NAME}",
        parse=lambda value: _extension(value, directory=directory),
    )
    return _Project(
        name=name,
        schema=schema,
        documents=documents,
        package_location=package_location,
        config=config,
    )


def _extension(
    value: object, /, *, directory: PurePosixPath
) -> tuple[PackageLocation, Config]:
    unread = parse_dict(value)

    module_root = required(unread, "moduleRoot", parse_string)
    package_location = PackageLocation(
        module_root=PurePosixPath(posixpath.normpath(directory / module_root)),
        package=required(unread, "package", parse_string),
    )
    document_sibling_module = optional(
        unread,
        "documentSiblingModule",
        lambda value: DocumentSiblingModule(
            name=parse_string(value), package_location=package_location
        ),
        default=None,
    )
    injector_names = optional(
        unread, "injectorNames", parse_strings, default=frozenset()
    )
    non_null_directive_name = optional(
        unread, "nonNullDirectiveName", parse_string, default=None
    )
    scalars = optional(unread, "scalars", _scalars, default={})
    struct_interface_name = optional(
        unread, "structInterfaceName", parse_string, default=None
    )

    check_all_read(unread)
    return package_location, Config(
        document_sibling_module=document_sibling_module,
        injector_names=injector_names,
        non_null_directive_name=non_null_directive_name,
        scalars=scalars,
        struct_interface_name=struct_interface_name,
    )


def _scalars(value: object, /) -> dict[str, Scalar]:
    scalars = parse_dict(value)
    return {
        scalar_name: required(scalars, scalar_name, _scalar)
        for scalar_name in list(scalars)
    }


def _scalar(value: object, /) -> Scalar:
    unread = parse_dict(value)
    type_ = required(unread, "type", parse_string)
    codec = optional(unread, "codec", _codec, default=None)
    check_all_read(unread)
    return Scalar(type=type_, codec=codec)


def _codec(value: object, /) -> Codec:
    unread = parse_dict(value)
    decode = required(unread, "decode", parse_string)
    encode = required(unread, "encode", parse_string)
    check_all_read(unread)
    return Codec(decode=decode, encode=encode)

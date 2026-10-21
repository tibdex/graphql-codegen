import ast
from collections.abc import Sequence
from dataclasses import dataclass
from typing import assert_never, final

from graphql_codegen._generator._ast_nodes import import_as
from graphql_codegen._generator._naming import (
    ABC,
    BUILTINS,
    COMPAT,
    INJECTION,
    OMITTED,
    OPERATION_TYPE,
    REFLECTION,
    RUNTIME,
    SCHEMA,
    TYPING,
)
from graphql_codegen._generator.spelling import ast_str

SCHEMA_MODULE = "schema"
DOCUMENT_PACKAGE = "document"
INJECTION_MODULE = "injection"
RUNTIME_PACKAGE = "runtime"


@final
@dataclass(frozen=True, kw_only=True)
class PackageModule:
    path: tuple[str, ...]


@final
@dataclass(frozen=True, kw_only=True)
class OutsideModule:
    package: str


type GeneratedModule = PackageModule | OutsideModule
"""A module in the package imports its modules relatively, so that the package works wherever it is written, and a module outside it absolutely."""


def _import_from(
    module: str | None, /, *, name: str, alias: str, level: int
) -> ast.ImportFrom:
    return ast.ImportFrom(
        module=module, names=[ast.alias(name=name, asname=alias)], level=level
    )


def _from_package(
    submodule: str | None, /, *, name: str, alias: str, module: GeneratedModule
) -> ast.ImportFrom:
    match module:
        case PackageModule(path=path):
            return _import_from(submodule, name=name, alias=alias, level=len(path) + 1)
        case OutsideModule(package=package):
            return _import_from(
                package if submodule is None else f"{package}.{submodule}",
                name=name,
                alias=alias,
                level=0,
            )
        case _ as never:
            assert_never(never)


def helper_imports(
    statements: Sequence[ast.stmt],
    /,
    *,
    module: GeneratedModule,
) -> list[ast.stmt]:
    used = {
        node.id
        for statement in statements
        for node in ast.walk(statement)
        if isinstance(node, ast.Name)
    }
    imports: list[ast.stmt] = []

    for helper, module_name in (
        (BUILTINS, "builtins"),
        (ABC, "collections.abc"),
        (TYPING, "typing"),
    ):
        if ast_str(helper) in used:
            imports.append(import_as(module_name, helper))

    for helper, submodule, imported in (
        # The runtime's shim, so that `typing_extensions` is only needed before Python 3.15.
        (COMPAT, RUNTIME_PACKAGE, "_compat"),
        (INJECTION, RUNTIME_PACKAGE, "injection"),
        (OPERATION_TYPE, RUNTIME_PACKAGE, "_operation_type"),
        (REFLECTION, RUNTIME_PACKAGE, "_reflection"),
        (RUNTIME, None, RUNTIME_PACKAGE),
        (OMITTED, RUNTIME_PACKAGE, "OMITTED"),
        (SCHEMA, None, SCHEMA_MODULE),
    ):
        if ast_str(helper) in used:
            imports.append(
                _from_package(
                    submodule, name=imported, alias=ast_str(helper), module=module
                )
            )

    return imports

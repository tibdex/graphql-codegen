import ast
from collections.abc import Sequence
from dataclasses import dataclass
from typing import assert_never, final

from graphql_codegen._generator._ast_nodes import import_as
from graphql_codegen._generator._naming import (
    ABC,
    BUILTINS,
    COMPAT,
    ENUM,
    INJECTION,
    INPUT,
    OMITTED,
    REFLECTION,
    RUNTIME,
    SCALAR,
    TYPING,
    SchemaSpellings,
)
from graphql_codegen._generator.spelling import ast_str

SCHEMA_PACKAGE = "schema"
DOCUMENT_PACKAGE = "document"
SCALAR_MODULE = "_scalar"
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
"""A generated module, by where it sits, which decides how it imports the package's own modules.

- :class:`PackageModule`: relatively, so that the package works wherever it is written.
- :class:`OutsideModule`: absolutely, since a relative import cannot leave its top-level package.

"""


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
            # How far up the package is.
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
    schema_spellings: SchemaSpellings,
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

    # From the runtime's copy, so that the package needs `typing_extensions` only before Python 3.15.
    if ast_str(COMPAT) in used:
        imports.append(
            _from_package(
                RUNTIME_PACKAGE, name="_compat", alias=ast_str(COMPAT), module=module
            )
        )

    if ast_str(INJECTION) in used:
        imports.append(
            _from_package(
                RUNTIME_PACKAGE,
                name="injection",
                alias=ast_str(INJECTION),
                module=module,
            )
        )

    if ast_str(REFLECTION) in used:
        imports.append(
            _from_package(
                RUNTIME_PACKAGE,
                name="_reflection",
                alias=ast_str(REFLECTION),
                module=module,
            )
        )

    if ast_str(RUNTIME) in used:
        imports.append(
            _from_package(
                None, name=RUNTIME_PACKAGE, alias=ast_str(RUNTIME), module=module
            )
        )

    if ast_str(OMITTED) in used:
        imports.append(
            _from_package(
                RUNTIME_PACKAGE, name="OMITTED", alias=ast_str(OMITTED), module=module
            )
        )

    for helper, schema_module in (
        (ENUM, schema_spellings.enum_module),
        (INPUT, schema_spellings.input_module),
    ):
        if ast_str(helper) in used:
            imports.append(
                # A module of the `schema` package reaches its siblings directly.
                _import_from(None, name=schema_module, alias=ast_str(helper), level=1)
                if module == PackageModule(path=(SCHEMA_PACKAGE,))
                else _from_package(
                    SCHEMA_PACKAGE,
                    name=schema_module,
                    alias=ast_str(helper),
                    module=module,
                ),
            )

    if ast_str(SCALAR) in used:
        imports.append(
            _from_package(
                None, name=SCALAR_MODULE, alias=ast_str(SCALAR), module=module
            )
        )

    return imports

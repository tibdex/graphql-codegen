from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Final, final

from graphql_codegen._generator.spelling import (
    PendingSpelling,
    SettledSpelling,
    settle_names,
)

COMPAT: Final = PendingSpelling("_compat")
TYPING: Final = PendingSpelling("_typing")
ABC: Final = PendingSpelling("_abc")
BUILTINS: Final = PendingSpelling("_builtins")
RUNTIME: Final = PendingSpelling("_runtime")
SCHEMA: Final = PendingSpelling("_schema")
INJECTION: Final = PendingSpelling("_injection")
OPERATION_TYPE: Final = PendingSpelling("_operation_type")
REFLECTION: Final = PendingSpelling("_reflection")
OMITTED: Final = PendingSpelling("_OMITTED")
"""Imported by name: pyright only takes a sentinel as a type when it is spelled bare."""


def nested_type_name(path: Sequence[str], /) -> str:
    """Private, since a fragment is the GraphQL way to name a shape, and prefixed by the root, so that a type checker's message names the operation."""
    return f"_{'_'.join(path)}"


@final
@dataclass(frozen=True, kw_only=True)
class OperationSpellings:
    operation: SettledSpelling
    variables: SettledSpelling
    data: SettledSpelling


def operation_spellings(
    operations: Sequence[SettledSpelling], /, *, taken: Collection[SettledSpelling]
) -> dict[SettledSpelling, OperationSpellings]:
    """An operation keeps its name, so that a clash suffixes its type: `Get`'s data type is `GetData_1` beside an operation `GetData`."""
    types = settle_names(
        [
            f"{operation}{suffix}"
            for operation in operations
            for suffix in ("Variables", "Data")
        ],
        taken={*operations, *taken},
    )
    return {
        operation: OperationSpellings(
            operation=operation,
            variables=types[f"{operation}Variables"],
            data=types[f"{operation}Data"],
        )
        for operation in operations
    }

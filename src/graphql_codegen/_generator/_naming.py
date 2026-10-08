from collections.abc import Collection, Mapping, Sequence
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
ENUM: Final = PendingSpelling("_enum")
INPUT: Final = PendingSpelling("_input")
INJECTION: Final = PendingSpelling("_injection")
REFLECTION: Final = PendingSpelling("_reflection")
SCALAR: Final = PendingSpelling("_scalar")
OMITTED: Final = PendingSpelling("_OMITTED")
"""The runtime's sentinel, imported by name: pyright only takes a sentinel as a type when it is spelled as a bare name."""


def nested_type_name(path: Sequence[str], /) -> str:
    """Private, since the GraphQL way for code to name a shape is a fragment.

    Prefixed by the root, so that a type checker message naming the type also names its operation or fragment.
    """
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
    """The operations keep theirs, so that a type is suffixed rather than an operation: `Get`'s data type is `GetData_1` in a module also holding an operation `GetData`."""
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


@final
@dataclass(frozen=True, kw_only=True)
class SchemaSpellings:
    type_spellings: Mapping[str, SettledSpelling]

    enum_module: SettledSpelling
    input_module: SettledSpelling


def schema_spellings(type_names: Collection[str], /) -> SchemaSpellings:
    types = settle_names(sorted(type_names), taken=set())
    # The package re-exports every type, so its modules must not be spelled like any of them.
    modules = settle_names(["enum", "input"], taken=set(types.values()))
    return SchemaSpellings(
        type_spellings=types,
        enum_module=modules["enum"],
        input_module=modules["input"],
    )

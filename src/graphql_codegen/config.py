from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import final

from graphql import GraphQLError, assert_name

from graphql_codegen.document_sibling_module import DocumentSiblingModule
from graphql_codegen.scalar import Scalar


@final
@dataclass(frozen=True, kw_only=True)
class Config:
    document_sibling_module: DocumentSiblingModule | None = None
    """When `None`, each document's operations and fragments go to a module named after it in the generated package's `document` subpackage, which re-exports them all."""

    injector_names: Collection[str] = frozenset()
    """The names of the variables and input fields that injectors supply.

    They are left out of the generated types, and must have the same type wherever they appear, nullability aside.
    """

    non_null_directive_name: str | None = None
    """The name of the client directive asserting that a field is not null, stripped from the document before it is sent."""

    scalars: Mapping[str, Scalar] = field(default_factory=dict)
    """The Python type, and codec, of each scalar by its GraphQL name."""

    struct_interface_name: str | None = None
    """The name of the interface every struct implements: a type whose single field carries a JSON payload.

    The payload is typed by the input type the struct is named after: `BookFilter` for `BookFilterStruct` when the interface is `Struct`.
    """

    def __post_init__(self) -> None:
        if self.non_null_directive_name is not None:
            try:
                assert_name(self.non_null_directive_name)
            except GraphQLError as error:
                raise ValueError(
                    f"Cannot name the client directive `{self.non_null_directive_name}`: {error.message}"
                ) from error

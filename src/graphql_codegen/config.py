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
    """When ``None``, each document's module is named after the document, in the generated package's `document` subpackage, which re-exports every operation and fragment."""

    injector_names: Collection[str] = frozenset()
    """The names of the variables and input fields an injector supplies, left out of the generated types and injected wherever an operation holds them.

    Every variable and input field of a same name must have the same type, nullability aside.
    """

    non_null_directive_name: str | None = None
    """The name of the client directive asserting that a nullable field is not null for this operation, stripped before a document goes on the wire."""

    scalars: Mapping[str, Scalar] = field(default_factory=dict)
    """The Python type of each scalar by GraphQL name."""

    struct_interface_name: str | None = None
    """The name of the interface every struct implements: an object type whose single field, of a custom scalar type, carries a JSON payload.

    The payload is shaped like the input type named as the struct without the interface's name, such as ``"BookFilter"`` for ``f"BookFilter{struct_interface_name}"``.
    """

    def __post_init__(self) -> None:
        if self.non_null_directive_name is not None:
            try:
                assert_name(self.non_null_directive_name)
            except GraphQLError as error:
                raise ValueError(
                    f"Cannot name the client directive `{self.non_null_directive_name}`: {error.message}"
                ) from error

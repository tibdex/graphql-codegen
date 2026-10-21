import ast
from collections.abc import Mapping
from typing import Final, Literal, assert_never, final

from graphql import (
    GraphQLBoolean,
    GraphQLEnumType,
    GraphQLFloat,
    GraphQLID,
    GraphQLInputObjectType,
    GraphQLInt,
    GraphQLList,
    GraphQLNonNull,
    GraphQLScalarType,
    GraphQLString,
    GraphQLType,
    is_introspection_type,
)

from graphql_codegen._generator._ast_nodes import (
    constant,
    name,
    optional,
    qualified,
    subscript,
)
from graphql_codegen._generator._naming import (
    ABC,
    BUILTINS,
    SCHEMA,
    TYPING,
)
from graphql_codegen._generator.spelling import SettledSpelling

# A config may override them.
BUILT_IN_SCALARS: Final[Mapping[str, str]] = {
    GraphQLBoolean.name: "bool",
    GraphQLFloat.name: "float",
    GraphQLID.name: "str",
    GraphQLInt.name: "int",
    GraphQLString.name: "str",
}


type Position = Literal["input", "data"]
"""An input list is a `Sequence`, so that a caller may pass a tuple, and a data list is a `list`, as `json.loads()` produces."""


def builtin(type_name: str, /) -> ast.expr:
    return qualified(BUILTINS, type_name)


@final
class AnnotationBuilder:
    def __init__(
        self,
        *,
        type_spellings: Mapping[str, SettledSpelling],
    ) -> None:
        self._type_spellings: Final = type_spellings

    def build(
        self,
        type_: GraphQLType,
        /,
        *,
        position: Position,
        object_annotation: ast.expr | None,
        bare_schema_types: bool,
    ) -> ast.expr:
        if isinstance(type_, GraphQLNonNull):
            return self._build_non_null(
                type_.of_type,
                position=position,
                object_annotation=object_annotation,
                bare_schema_types=bare_schema_types,
            )

        return optional(
            self._build_non_null(
                type_,
                position=position,
                object_annotation=object_annotation,
                bare_schema_types=bare_schema_types,
            ),
        )

    def _build_non_null(
        self,
        type_: GraphQLType,
        /,
        *,
        position: Position,
        object_annotation: ast.expr | None,
        bare_schema_types: bool,
    ) -> ast.expr:
        match type_:
            case GraphQLList():
                element = self.build(
                    type_.of_type,
                    position=position,
                    object_annotation=object_annotation,
                    bare_schema_types=bare_schema_types,
                )
                match position:
                    case "input":
                        container = qualified(ABC, "Sequence")
                    case "data":
                        container = builtin("list")
                    case _ as never:
                        assert_never(never)

                return subscript(container, element)
            case GraphQLScalarType() if type_.name not in self._type_spellings:
                return builtin(BUILT_IN_SCALARS.get(type_.name, "object"))
            case GraphQLEnumType() if is_introspection_type(type_):
                # An introspection enum is not in the package, so it is spelled inline.
                return subscript(
                    qualified(TYPING, "Literal"),
                    *(constant(value_name) for value_name in type_.values),
                )
            case GraphQLScalarType() | GraphQLEnumType() | GraphQLInputObjectType():
                spelling = self._type_spellings[type_.name]
                return (
                    name(spelling) if bare_schema_types else qualified(SCHEMA, spelling)
                )
            case _:
                assert object_annotation is not None, (
                    f"Expected the caller to name the type for `{type_}`."
                )
                return object_annotation

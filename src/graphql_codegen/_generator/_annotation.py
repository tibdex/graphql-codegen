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
    ENUM,
    INPUT,
    SCALAR,
    TYPING,
    SchemaSpellings,
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
        scalar_spellings: Mapping[str, SettledSpelling],
        schema_spellings: SchemaSpellings,
    ) -> None:
        self._scalar_spellings: Final = scalar_spellings
        self._type_spellings: Final = schema_spellings.type_spellings

    def build(
        self,
        type_: GraphQLType,
        /,
        *,
        position: Position,
        object_annotation: ast.expr | None,
        bare_inputs: bool,
    ) -> ast.expr:
        if isinstance(type_, GraphQLNonNull):
            return self._build_non_null(
                type_.of_type,
                position=position,
                object_annotation=object_annotation,
                bare_inputs=bare_inputs,
            )

        return optional(
            self._build_non_null(
                type_,
                position=position,
                object_annotation=object_annotation,
                bare_inputs=bare_inputs,
            ),
        )

    def _build_non_null(
        self,
        type_: GraphQLType,
        /,
        *,
        position: Position,
        object_annotation: ast.expr | None,
        bare_inputs: bool,
    ) -> ast.expr:
        match type_:
            case GraphQLList():
                element = self.build(
                    type_.of_type,
                    position=position,
                    object_annotation=object_annotation,
                    bare_inputs=bare_inputs,
                )
                match position:
                    case "input":
                        container = qualified(ABC, "Sequence")
                    case "data":
                        container = builtin("list")
                    case _ as never:
                        assert_never(never)

                return subscript(container, element)
            case GraphQLScalarType():
                return self._scalar_annotation(type_.name)
            case GraphQLEnumType() if is_introspection_type(type_):
                # An introspection enum is not in the package, so it is spelled inline.
                return subscript(
                    qualified(TYPING, "Literal"),
                    *(constant(value_name) for value_name in type_.values),
                )
            case GraphQLEnumType():
                return qualified(ENUM, self._type_spellings[type_.name])
            case GraphQLInputObjectType():
                spelling = self._type_spellings[type_.name]
                return name(spelling) if bare_inputs else qualified(INPUT, spelling)
            case _:
                assert object_annotation is not None, (
                    f"Expected the caller to name the type for `{type_}`."
                )
                return object_annotation

    def _scalar_annotation(self, scalar_name: str, /) -> ast.expr:
        if scalar_name in self._scalar_spellings:
            return qualified(SCALAR, self._scalar_spellings[scalar_name])

        return builtin(BUILT_IN_SCALARS.get(scalar_name, "object"))

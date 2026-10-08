import ast
from collections.abc import Mapping, Set as AbstractSet
from functools import reduce
from typing import cast

from graphql import (
    GraphQLError,
    GraphQLInputObjectType,
    GraphQLInputType,
    GraphQLList,
    GraphQLNonNull,
    GraphQLSchema,
    GraphQLType,
    OperationDefinitionNode,
    VariableDefinitionNode,
    get_named_type,
    is_list_type,
    is_non_null_type,
    type_from_ast,
)

from graphql_codegen._generator._annotation import AnnotationBuilder
from graphql_codegen._generator._ast_nodes import (
    docstring,
    name,
    qualified,
    subscript,
    union,
)
from graphql_codegen._generator._naming import ABC, INJECTION, OMITTED
from graphql_codegen._generator._schema import as_non_null
from graphql_codegen._generator._typed_dict import Key, emit_closed
from graphql_codegen._generator.spelling import SettledSpelling


def get_injections(
    operation: OperationDefinitionNode,
    /,
    *,
    schema: GraphQLSchema,
    injector_names: AbstractSet[str],
) -> dict[str, dict[tuple[str, ...], GraphQLInputType]]:
    """An injected variable goes in the variables themselves, `()`.

    An injected field goes in every input object holding it, at any depth, since a mutation takes the transaction it belongs to inside its input rather than beside it.
    A static path can say neither "every element of this list" nor the unbounded depth of a recursive input type, so an injected field reached that way fails generation rather than being silently left out.
    """
    injections: dict[str, dict[tuple[str, ...], GraphQLInputType]] = {}

    def holds_injected_field(
        input_type: GraphQLInputObjectType, /, *, seen: set[str]
    ) -> bool:
        if input_type.name in seen:
            return False

        seen.add(input_type.name)
        return any(
            field_name in injector_names
            or (
                isinstance(
                    named_type := get_named_type(field.type), GraphQLInputObjectType
                )
                and holds_injected_field(named_type, seen=seen)
            )
            for field_name, field in input_type.fields.items()
        )

    def visit_input(
        input_type: GraphQLInputObjectType,
        wrapped_type: GraphQLType,
        /,
        *,
        path: tuple[str, ...],
        ancestors: tuple[str, ...],
        definition: VariableDefinitionNode,
    ) -> None:
        nullable_type = (
            wrapped_type.of_type
            if isinstance(wrapped_type, GraphQLNonNull)
            else wrapped_type
        )

        if is_list_type(nullable_type) or input_type.name in ancestors:
            if holds_injected_field(input_type, seen=set()):
                raise GraphQLError(
                    f"Cannot inject into `${path[0]}`: an injected field is reached through a list or a recursive input type, which no static path can express.",
                    nodes=definition,
                )
            return

        for field_name, field in input_type.fields.items():
            if field_name in injector_names:
                injections.setdefault(field_name, {})[path] = field.type
                continue

            named_type = get_named_type(field.type)

            if isinstance(named_type, GraphQLInputObjectType):
                visit_input(
                    named_type,
                    field.type,
                    path=(*path, field_name),
                    ancestors=(*ancestors, input_type.name),
                    definition=definition,
                )

    for definition in operation.variable_definitions or ():
        variable_name = definition.variable.name.value

        variable_type = type_from_ast(schema, definition.type)
        assert variable_type is not None, (
            "Validation guarantees that a variable's type exists."
        )

        if variable_name in injector_names:
            # Validation guarantees that a variable's type is an input type.
            injections.setdefault(variable_name, {})[()] = cast(
                "GraphQLInputType", variable_type
            )
            continue
        named_type = get_named_type(variable_type)

        if isinstance(named_type, GraphQLInputObjectType):
            visit_input(
                named_type,
                variable_type,
                path=(variable_name,),
                ancestors=(),
                definition=definition,
            )

    return injections


def get_injected_types(
    injections: Mapping[str, Mapping[str, Mapping[tuple[str, ...], GraphQLInputType]]],
    /,
    *,
    injector_names: AbstractSet[str],
) -> dict[str, GraphQLInputType]:
    """An injector supplies one value for every position it is injected into, so they must all have the same type, nullability aside.

    Its type is then the strictest: non-null wherever any position is, since a non-null value suits a nullable position too, while an injector cannot tell which position it is called for.
    """
    positions: dict[str, dict[str, str]] = {}
    types: dict[str, list[GraphQLInputType]] = {}

    for operation_name, operation_injections in injections.items():
        for injector_name, targets in operation_injections.items():
            for path, type_ in targets.items():
                position = "$" + ".".join((*path, injector_name))
                # Spelled without `!`, so that positions differing only by nullability agree.
                spelling = str(type_).replace("!", "")
                positions.setdefault(injector_name, {}).setdefault(
                    spelling, f"`{position}` in `{operation_name}`"
                )
                types.setdefault(injector_name, []).append(type_)

    for injector_name, by_type in positions.items():
        if len(by_type) > 1:
            raise GraphQLError(
                f"Expected `{injector_name}` to have one type, nullability aside, wherever it is injected, but it is "
                + " and ".join(
                    f"`{type_}` at {position}" for type_, position in by_type.items()
                )
                + "."
            )

    # An injector injected nowhere, a misspelled one for instance, has no position to take its type from.
    if nowhere := sorted(injector_names - types.keys()):
        raise ValueError(
            f"Expected `{nowhere[0]}` to be injected somewhere, but no operation sends a variable or an input field of that name."
        )

    return {
        injector_name: reduce(_strictest, injector_types)
        for injector_name, injector_types in types.items()
    }


def _strictest(left: GraphQLInputType, right: GraphQLInputType, /) -> GraphQLInputType:
    non_null = isinstance(left, GraphQLNonNull) or isinstance(right, GraphQLNonNull)
    left = left.of_type if isinstance(left, GraphQLNonNull) else left
    right = right.of_type if isinstance(right, GraphQLNonNull) else right
    merged = (
        GraphQLList(_strictest(left.of_type, right.of_type))
        if isinstance(left, GraphQLList) and isinstance(right, GraphQLList)
        else left
    )
    return as_non_null(merged) if non_null else merged


def emit_injector_module(
    injected_types: Mapping[str, GraphQLInputType], /, *, annotations: AnnotationBuilder
) -> list[ast.stmt]:
    """The mapping of functions is a :class:`typing.TypedDict`, rather than keyword arguments, so that a name no keyword can spell is injected too, and a misspelled name fails type checking with every type checker.

    It builds the runtime's private :class:`~graphql_codegen.runtime.injection._Injectors` rather than being given to a client, so that a client only calls injectors that were type checked.
    """
    functions_spelling, function_spelling = (
        SettledSpelling("InjectorFunctions"),
        SettledSpelling("injectors"),
    )
    # Local to the function, so that nothing can clash with it.
    functions = SettledSpelling("functions")
    keys: list[Key] = []

    for injector_name, type_ in sorted(injected_types.items()):
        value = annotations.build(
            type_, position="input", object_annotation=None, bare_inputs=False
        )

        # A nullable input accepts omission too, while a non-null one accepts neither.
        if not is_non_null_type(type_):
            value = union(value, name(OMITTED))

        keys.append(
            Key(
                name=injector_name,
                annotation=subscript(
                    qualified(ABC, "Callable"), ast.List(elts=[], ctx=ast.Load()), value
                ),
                required=False,
                description="",
            ),
        )

    return [
        *emit_closed(
            functions_spelling,
            keys,
            description="The functions supplying each injected value, by name.\n\nWhere the value may be null, one returning `OMITTED` leaves it out, and one returning `None` sends `null`.",
            bare_names=frozenset({functions_spelling, function_spelling}),
        ),
        ast.FunctionDef(
            name=function_spelling,
            args=ast.arguments(
                posonlyargs=[
                    ast.arg(arg=functions, annotation=name(functions_spelling))
                ],
                args=[],
                kwonlyargs=[],
                kw_defaults=[],
                defaults=[],
            ),
            body=[
                docstring(
                    "Return what a client calls to supply the injected values, each serialized as its type says."
                ),
                ast.Return(
                    value=ast.Call(
                        func=qualified(INJECTION, "_Injectors"),
                        args=[name(functions)],
                        keywords=[
                            ast.keyword(
                                arg="injector_functions_type",
                                value=name(functions_spelling),
                            )
                        ],
                    ),
                ),
            ],
            decorator_list=[],
            returns=qualified(INJECTION, "_Injectors"),
            type_params=[],
        ),
    ]

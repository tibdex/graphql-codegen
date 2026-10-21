import ast
from collections.abc import Mapping, Sequence, Set as AbstractSet

from graphql import (
    DocumentNode,
    FieldNode,
    GraphQLEnumType,
    GraphQLInputField,
    GraphQLInputObjectType,
    GraphQLNamedType,
    GraphQLScalarType,
    GraphQLSchema,
    TypeInfo,
    TypeInfoVisitor,
    Undefined,
    VariableDefinitionNode,
    Visitor,
    get_named_type,
    is_non_null_type,
    type_from_ast,
    visit,
)

from graphql_codegen._generator._annotation import AnnotationBuilder
from graphql_codegen._generator._ast_nodes import (
    constant,
    documented,
    name,
    qualified,
    subscript,
    type_alias,
    union,
)
from graphql_codegen._generator._naming import TYPING
from graphql_codegen._generator._scalar import emit_scalar_aliases
from graphql_codegen._generator._schema import as_non_null
from graphql_codegen._generator._structs import StructTypes, get_struct_input_name
from graphql_codegen._generator._typed_dict import Key, emit_closed
from graphql_codegen._generator.spelling import (
    PendingSpelling,
    SettledSpelling,
    ast_str,
)
from graphql_codegen.scalar import Scalar


def _is_required(field: GraphQLInputField, /) -> bool:
    """A non-null field with a default is optional, since the server supplies the default."""
    # graphql-core holds a Python default in `default_value`, and an SDL literal one in `default`.
    return (
        is_non_null_type(field.type)
        and field.default_value is Undefined
        and field.default is None
    )


def get_reached_type_names(
    document: DocumentNode,
    /,
    *,
    schema: GraphQLSchema,
    struct_types: StructTypes | None,
    injector_names: AbstractSet[str],
) -> frozenset[str]:
    pending: list[GraphQLNamedType] = []
    type_info = TypeInfo(schema)

    class _Visitor(Visitor):
        def enter_variable_definition(
            self, node: VariableDefinitionNode, *_args: object
        ) -> None:
            variable_type = type_from_ast(schema, node.type)
            assert variable_type is not None, (
                "Validation guarantees that a variable's type exists."
            )
            pending.append(get_named_type(variable_type))

        def enter_field(self, node: FieldNode, *_args: object) -> None:
            field_type = type_info.get_type()
            parent_type = type_info.get_parent_type()
            assert field_type is not None and parent_type is not None, (
                "Validation guarantees that a selected field exists."
            )

            pending.append(get_named_type(field_type))
            struct_input_name = get_struct_input_name(
                parent_type,
                node.name.value,
                struct_types=struct_types,
            )

            if struct_input_name is not None:
                pending.append(schema.type_map[struct_input_name])

    visit(document, TypeInfoVisitor(type_info, _Visitor()))

    reached: set[str] = set()

    while pending:
        named_type = pending.pop()

        if (
            named_type.name in reached
            or named_type.name.startswith("__")
            or not isinstance(named_type, GraphQLEnumType | GraphQLInputObjectType)
        ):
            continue

        reached.add(named_type.name)

        if isinstance(named_type, GraphQLInputObjectType):
            pending.extend(
                get_named_type(field.type)
                for field_name, field in named_type.fields.items()
                if field_name not in injector_names
            )

    return frozenset(reached)


def _emit_one_of(
    input_type: GraphQLInputObjectType,
    /,
    *,
    fields: Mapping[str, GraphQLInputField],
    annotations: AnnotationBuilder,
    spelling: SettledSpelling,
    bare_names: frozenset[str],
) -> list[ast.stmt]:
    """`closed=True` is what rejects a value with two keys: `total=` only governs the declared keys."""
    member_spellings = {
        field_name: PendingSpelling(f"_{spelling}_{field_name}")
        for field_name in fields
    }
    assert member_spellings, f"`{input_type.name}` is `@oneOf` but has no field."
    # The union first: a `type` statement is lazy, so a member naming it, as a recursive filter does, finds it bound.
    body = documented(
        type_alias(spelling, union(*map(name, member_spellings.values()))),
        text=input_type.description or "",
    )

    for field_name, field in fields.items():
        body.extend(
            emit_closed(
                member_spellings[field_name],
                [
                    Key(
                        name=field_name,
                        # The spec declares every member nullable but rejects null, so the set member is never `None`.
                        annotation=annotations.build(
                            as_non_null(field.type),
                            position="input",
                            bare_schema_types=True,
                            object_annotation=None,
                        ),
                        description=field.description or "",
                        required=True,
                    ),
                ],
                description="",
                bare_names=bare_names,
            ),
        )

    return body


def emit_schema_module(
    schema: GraphQLSchema,
    /,
    *,
    annotations: AnnotationBuilder,
    type_spellings: Mapping[str, SettledSpelling],
    scalars: Mapping[str, Scalar],
    injector_names: AbstractSet[str],
) -> list[ast.stmt]:
    # A single module for every kind, since no two types of a schema share a name: https://spec.graphql.org/September2025/#sec-Schema
    # Every type is bound bare in the module, so no key may shadow one in a class body.
    bare_names = frozenset(type_spellings.values())
    imports, scalar_aliases = emit_scalar_aliases(scalars, spellings=type_spellings)
    body: list[ast.stmt] = []

    # In the schema's order, which graphql-core keeps.
    for type_ in schema.type_map.values():
        match type_:
            case GraphQLScalarType() if type_.name in scalar_aliases:
                body.append(scalar_aliases[type_.name])
            case GraphQLEnumType() if type_.name in type_spellings:
                body.extend(
                    documented(
                        type_alias(
                            type_spellings[type_.name],
                            subscript(
                                qualified(TYPING, "Literal"),
                                *(constant(member) for member in type_.values),
                            ),
                        ),
                        text=type_.description or "",
                    )
                )
            case GraphQLInputObjectType() if type_.name in type_spellings:
                body.extend(
                    _emit_input(
                        type_,
                        annotations=annotations,
                        spelling=type_spellings[type_.name],
                        injector_names=injector_names,
                        bare_names=bare_names,
                    )
                )
            case _:
                pass

    return [
        *imports,
        *_defer_forward_references(body, schema_names=bare_names),
        *body,
    ]


def _emit_input(
    input_type: GraphQLInputObjectType,
    /,
    *,
    annotations: AnnotationBuilder,
    spelling: SettledSpelling,
    injector_names: AbstractSet[str],
    bare_names: frozenset[str],
) -> list[ast.stmt]:
    fields = {
        field_name: field
        for field_name, field in input_type.fields.items()
        if field_name not in injector_names
    }

    if input_type.is_one_of:
        return _emit_one_of(
            input_type,
            fields=fields,
            annotations=annotations,
            spelling=spelling,
            bare_names=bare_names,
        )

    return emit_closed(
        spelling,
        [
            Key(
                name=field_name,
                annotation=annotations.build(
                    field.type,
                    position="input",
                    bare_schema_types=True,
                    object_annotation=None,
                ),
                required=_is_required(field),
                description=field.description or "",
            )
            for field_name, field in fields.items()
        ],
        description=input_type.description or "",
        bare_names=bare_names,
    )


def _defer_forward_references(
    body: Sequence[ast.stmt], /, *, schema_names: frozenset[str]
) -> list[ast.stmt]:
    """A functional TypedDict's values are evaluated when the statement runs, unlike annotations, so a type defined later is referenced through a lazy `type` alias."""
    aliases: dict[SettledSpelling, PendingSpelling] = {}
    defined: set[str] = set()

    for statement in body:
        match statement:
            case ast.Assign(targets=[ast.Name(id=bound)], value=ast.Call() as call):
                for node in ast.walk(call):
                    if (
                        isinstance(node, ast.Name)
                        and node.id in schema_names
                        and node.id not in defined
                    ):
                        alias = aliases.setdefault(
                            SettledSpelling(node.id), PendingSpelling(f"_{node.id}_Ref")
                        )
                        node.id = ast_str(alias)

                defined.add(bound)
            case ast.TypeAlias(name=ast.Name(id=bound)) | ast.ClassDef(name=bound):
                defined.add(bound)
            case _:
                pass

    return [type_alias(alias, name(type_name)) for type_name, alias in aliases.items()]

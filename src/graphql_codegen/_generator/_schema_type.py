import ast
from collections.abc import Mapping, Set as AbstractSet

from graphql import (
    DocumentNode,
    FieldNode,
    GraphQLEnumType,
    GraphQLInputField,
    GraphQLInputObjectType,
    GraphQLNamedType,
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
    module,
    name,
    qualified,
    subscript,
    type_alias,
    union,
)
from graphql_codegen._generator._naming import TYPING, SchemaSpellings
from graphql_codegen._generator._schema import as_non_null
from graphql_codegen._generator._structs import StructTypes, get_struct_input_name
from graphql_codegen._generator._typed_dict import Key, emit_closed
from graphql_codegen._generator.spelling import (
    PendingSpelling,
    SettledSpelling,
    ast_str,
)


def _is_required(field: GraphQLInputField, /) -> bool:
    """A non-null field with a default is optional, since the server supplies the default."""
    # graphql-core holds a Python default in `default_value`, and an SDL literal one in `default`.
    return (
        is_non_null_type(field.type)
        and field.default_value is Undefined
        and field.default is None
    )


def _reached[Type: GraphQLNamedType](
    schema: GraphQLSchema, kind: type[Type], /, *, schema_spellings: SchemaSpellings
) -> list[Type]:
    return [
        type_
        for type_ in schema.type_map.values()
        if isinstance(type_, kind) and type_.name in schema_spellings.type_spellings
    ]


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


def emit_enums(
    schema: GraphQLSchema, /, *, schema_spellings: SchemaSpellings
) -> list[ast.stmt]:
    return [
        statement
        for enum_type in _reached(
            schema, GraphQLEnumType, schema_spellings=schema_spellings
        )
        for statement in documented(
            type_alias(
                schema_spellings.type_spellings[enum_type.name],
                subscript(
                    qualified(TYPING, "Literal"),
                    *(constant(member) for member in enum_type.values),
                ),
            ),
            text=enum_type.description or "",
        )
    ]


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
    body: list[ast.stmt] = []
    member_spellings: list[PendingSpelling] = []

    for field_name, field in fields.items():
        member_spelling = PendingSpelling(f"_{spelling}_{field_name}")
        member_spellings.append(member_spelling)
        body.extend(
            emit_closed(
                member_spelling,
                [
                    Key(
                        name=field_name,
                        # The spec declares every member nullable but rejects null, so the set member is never `None`.
                        annotation=annotations.build(
                            as_non_null(field.type),
                            position="input",
                            bare_inputs=True,
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

    assert member_spellings, f"`{input_type.name}` is `@oneOf` but has no field."
    body.extend(
        documented(
            type_alias(
                spelling,
                union(*(name(member_spelling) for member_spelling in member_spellings)),
            ),
            text=input_type.description or "",
        ),
    )
    return body


def emit_inputs(
    schema: GraphQLSchema,
    /,
    *,
    annotations: AnnotationBuilder,
    schema_spellings: SchemaSpellings,
    injector_names: AbstractSet[str],
) -> list[ast.stmt]:
    input_types = _reached(
        schema, GraphQLInputObjectType, schema_spellings=schema_spellings
    )
    bare_names = frozenset(
        schema_spellings.type_spellings[input_type.name] for input_type in input_types
    )
    body: list[ast.stmt] = []

    for input_type in input_types:
        fields = {
            field_name: field
            for field_name, field in input_type.fields.items()
            if field_name not in injector_names
        }

        if input_type.is_one_of:
            body.extend(
                _emit_one_of(
                    input_type,
                    fields=fields,
                    annotations=annotations,
                    spelling=schema_spellings.type_spellings[input_type.name],
                    bare_names=bare_names,
                ),
            )
            continue

        body.extend(
            emit_closed(
                schema_spellings.type_spellings[input_type.name],
                [
                    Key(
                        name=field_name,
                        annotation=annotations.build(
                            field.type,
                            position="input",
                            bare_inputs=True,
                            object_annotation=None,
                        ),
                        required=_is_required(field),
                        description=field.description or "",
                    )
                    for field_name, field in fields.items()
                ],
                description=input_type.description or "",
                bare_names=bare_names,
            ),
        )

    return [*_defer_functional_references(body, input_names=bare_names), *body]


def _defer_functional_references(
    body: list[ast.stmt], /, *, input_names: frozenset[SettledSpelling]
) -> list[ast.stmt]:
    """A functional TypedDict's values are evaluated when the statement runs, unlike annotations, so an input defined later is referenced through a lazy `type` alias."""
    aliases: dict[SettledSpelling, PendingSpelling] = {}

    for statement in body:
        if not (
            isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call)
        ):
            continue

        for node in ast.walk(statement.value):
            if isinstance(node, ast.Name) and node.id in input_names:
                alias = aliases.setdefault(
                    SettledSpelling(node.id), PendingSpelling(f"_{node.id}_Ref")
                )
                node.id = ast_str(alias)

    return [
        type_alias(alias, name(input_name)) for input_name, alias in aliases.items()
    ]


def emit_schema_package(
    schema: GraphQLSchema, /, *, schema_spellings: SchemaSpellings
) -> ast.Module:
    exports = {
        schema_spellings.enum_module: _reached(
            schema, GraphQLEnumType, schema_spellings=schema_spellings
        ),
        schema_spellings.input_module: _reached(
            schema, GraphQLInputObjectType, schema_spellings=schema_spellings
        ),
    }
    return module(
        [
            ast.ImportFrom(module=module_name, names=[ast.alias(name="*")], level=1)
            for module_name, types in exports.items()
            # An empty `from ... import` is a syntax error.
            if types
        ],
    )

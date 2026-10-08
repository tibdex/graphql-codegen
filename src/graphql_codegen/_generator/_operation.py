import ast
from collections.abc import Collection, Mapping, Sequence, Set as AbstractSet
from dataclasses import replace
from typing import Final

from graphql import (
    REMOVE,
    DirectiveNode,
    DocumentNode,
    ExecutableDefinitionNode,
    FragmentDefinitionNode,
    GraphQLSchema,
    OperationDefinitionNode,
    StringValueNode,
    VariableDefinitionNode,
    Visitor,
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
)
from graphql_codegen._generator._naming import (
    BUILTINS,
    RUNTIME,
    TYPING,
    OperationSpellings,
)
from graphql_codegen._generator._typed_dict import Key, emit_closed


def description_text(description: StringValueNode | None, /) -> str:
    return "" if description is None else description.value


def _undescribed[Node: ExecutableDefinitionNode | VariableDefinitionNode](
    node: Node, /
) -> Node | None:
    if node.description is None:
        return None

    return replace(node, description=None)


class _StripForServer(Visitor):
    """Remove what the server does not need: this library's directives, and descriptions.

    The spec says descriptions must not affect execution, so on the wire they would only cost bytes, and a server whose parser predates them would reject the whole request.
    They become docstrings instead.

    Block strings are printed as ordinary ones, with the same value, so that no token spans lines, since the runtime slices a merge template at blank lines, and so that a sigil in a string can be escaped, which a block string cannot do.
    """

    def __init__(self, directive_names: AbstractSet[str], /) -> None:
        super().__init__()
        self._directive_names: Final = directive_names

    def enter_directive(self, node: DirectiveNode, *_args: object) -> object:
        return REMOVE if node.name.value in self._directive_names else None

    def enter_string_value(self, node: StringValueNode, *_args: object) -> object:
        if not node.block:
            return None

        return replace(node, block=False)

    def enter_operation_definition(
        self, node: OperationDefinitionNode, *_args: object
    ) -> object:
        return _undescribed(node)

    def enter_fragment_definition(
        self, node: FragmentDefinitionNode, *_args: object
    ) -> object:
        return _undescribed(node)

    def enter_variable_definition(
        self, node: VariableDefinitionNode, *_args: object
    ) -> object:
        return _undescribed(node)


def strip_for_server(
    operation: OperationDefinitionNode,
    /,
    *,
    fragments: Sequence[FragmentDefinitionNode],
    client_directive_names: AbstractSet[str],
) -> DocumentNode:
    document = DocumentNode(definitions=(operation, *fragments))
    result = visit(document, _StripForServer(client_directive_names))
    assert isinstance(result, DocumentNode)
    return result


def emit_variables_type(
    operation: OperationDefinitionNode,
    /,
    *,
    spellings: OperationSpellings,
    schema: GraphQLSchema,
    annotations: AnnotationBuilder,
    injector_names: AbstractSet[str],
) -> list[ast.stmt]:
    """A variable is :data:`typing.Required` only when it is non-null *and* has no default, for the same reason as an input field: a default makes it optional for the client, and the document carries the default so Python never needs to."""
    keys: list[Key] = []

    for definition in operation.variable_definitions or ():
        variable_name = definition.variable.name.value

        if variable_name in injector_names:
            # Injected: the client inserts it, so a caller must not be able to.
            continue

        variable_type = type_from_ast(schema, definition.type)
        assert variable_type is not None, (
            "Validation guarantees that a variable's type exists."
        )
        keys.append(
            Key(
                name=variable_name,
                annotation=annotations.build(
                    variable_type,
                    position="input",
                    object_annotation=None,
                    bare_inputs=False,
                ),
                required=is_non_null_type(variable_type)
                and definition.default_value is None,
                description=description_text(definition.description),
            ),
        )

    return emit_closed(
        spellings.variables,
        keys,
        description="",
        bare_names=frozenset(),
    )


def _frozenset(elements: Sequence[ast.expr], /) -> ast.expr:
    return ast.Call(
        func=qualified(BUILTINS, "frozenset"),
        args=[ast.Set(elts=list(elements))],
        keywords=[],
    )


def emit_operation_constant(
    operation: OperationDefinitionNode,
    /,
    *,
    graphql_name: str,
    spellings: OperationSpellings,
    document: str,
    injections: Mapping[str, Collection[tuple[str, ...]]],
) -> list[ast.stmt]:
    """The operation's description is the constant's docstring, since the constant is what callers reach for.

    It is annotated although its type parameters could be inferred from the call: pyrefly widens an inferred `Literal['mutation']` to :class:`str` in a list comprehension passed to the clients' overloads, which then match none.
    The document is inlined rather than given a constant of its own: a triple quoted literal reads as GraphQL wherever it sits.
    """
    # In the order of the class's fields, which is the order things happen in.
    keywords = [
        ast.keyword(arg="operation_type", value=constant(operation.operation.value)),
        # The GraphQL name, which is what the server knows the operation by.
        ast.keyword(arg="name", value=constant(graphql_name)),
        ast.keyword(arg="document", value=constant(document)),
        # What crosses the wire is read off the variables and data types at runtime: they are the only description of it.
        ast.keyword(arg="variables_type", value=name(spellings.variables)),
    ]

    if injections:
        keywords.append(
            ast.keyword(
                arg="injections",
                value=ast.Dict(
                    keys=[constant(injector_name) for injector_name in injections],
                    values=[
                        _frozenset(
                            [
                                ast.Tuple(
                                    elts=[constant(part) for part in target],
                                    ctx=ast.Load(),
                                )
                                for target in targets
                            ],
                        )
                        for targets in injections.values()
                    ],
                ),
            ),
        )

    keywords.append(ast.keyword(arg="data_type", value=name(spellings.data)))

    # Not annotated `Final`: pyright would then reject the package re-exporting it from the module named like it.
    return documented(
        ast.AnnAssign(
            target=ast.Name(id=spellings.operation, ctx=ast.Store()),
            annotation=subscript(
                qualified(RUNTIME, "Operation"),
                subscript(
                    qualified(TYPING, "Literal"), constant(operation.operation.value)
                ),
                name(spellings.variables),
                name(spellings.data),
            ),
            value=ast.Call(
                func=qualified(RUNTIME, "Operation"), args=[], keywords=keywords
            ),
            simple=1,
        ),
        text=description_text(operation.description),
    )

import ast
from collections.abc import Sequence, Set as AbstractSet
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
)
from graphql_codegen._generator._injector import Injections
from graphql_codegen._generator._naming import (
    BUILTINS,
    OPERATION_TYPE,
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
    """Descriptions become docstrings instead of going on the wire, where a server whose parser predates them would reject the request.

    Block strings are printed as ordinary ones, since the runtime slices a merge template at blank lines and escapes a sigil in a string, which a block string cannot.
    """

    def __init__(self, directive_names: AbstractSet[str], /) -> None:
        super().__init__()
        self._directive_names: Final = directive_names

    def enter_directive(self, node: DirectiveNode, *_args: object) -> object:
        return REMOVE if node.name.value in self._directive_names else None

    def enter_string_value(self, node: StringValueNode, *_args: object) -> object:
        if node.block is not True:
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
    """A variable with a default is optional, since the document carries the default."""
    keys: list[Key] = []

    for definition in operation.variable_definitions or ():
        variable_name = definition.variable.name.value

        if variable_name in injector_names:
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
    injections: Injections,
) -> list[ast.stmt]:
    keywords = [
        ast.keyword(
            arg="operation_type",
            value=qualified(OPERATION_TYPE, operation.operation.value.upper()),
        ),
        ast.keyword(arg="name", value=constant(graphql_name)),
        ast.keyword(arg="document", value=constant(document)),
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
                                    elts=[constant(part) for part in path],
                                    ctx=ast.Load(),
                                )
                                for path in by_path
                            ],
                        )
                        for by_path in injections.values()
                    ],
                ),
            ),
        )

    keywords.append(ast.keyword(arg="data_type", value=name(spellings.data)))

    return documented(
        ast.AnnAssign(
            target=ast.Name(id=spellings.operation, ctx=ast.Store()),
            annotation=qualified(TYPING, "Final"),
            value=ast.Call(
                func=qualified(RUNTIME, "Operation"), args=[], keywords=keywords
            ),
            simple=1,
        ),
        text=description_text(operation.description),
    )

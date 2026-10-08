from collections.abc import Mapping

from graphql import (
    DocumentNode,
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLError,
    GraphQLSchema,
    InlineFragmentNode,
    NameNode,
    Node,
    OperationDefinitionNode,
    SelectionSetNode,
    TypeInfo,
    TypeInfoVisitor,
    Visitor,
    is_abstract_type,
    validate,
    visit,
)


def parse_operations(
    document: DocumentNode, /, *, schema: GraphQLSchema
) -> dict[str, OperationDefinitionNode]:
    errors = validate(schema, document)
    operations: dict[str, OperationDefinitionNode] = {}

    for definition in document.definitions:
        if not isinstance(definition, OperationDefinitionNode):
            continue

        if definition.name is None:
            errors.append(
                GraphQLError(
                    "Expected the operation to be named to name its constant after it.",
                    nodes=definition,
                )
            )
        else:
            operations[definition.name.value] = definition

    if errors:
        raise ExceptionGroup("The operations are invalid", errors)

    return operations


def insert_typename(
    document: DocumentNode, /, *, schema: GraphQLSchema
) -> DocumentNode:
    """Return *document* selecting `__typename` on every abstract type, since it tells the values apart, for type checkers to narrow and for the parser to convert."""
    type_info = TypeInfo(schema)

    class _Visitor(Visitor):
        def enter_selection_set(
            self, node: SelectionSetNode, _key: object, parent: object, *_args: object
        ) -> SelectionSetNode | None:
            # An inline fragment's selections are gathered into the branches of the selection enclosing it, which that selection's `__typename` tells apart.
            if (
                isinstance(parent, InlineFragmentNode)
                or not is_abstract_type(type_info.get_parent_type())
                or any(
                    isinstance(selection, FieldNode)
                    and selection.name.value == "__typename"
                    and selection.alias is None
                    and not selection.directives
                    for selection in node.selections
                )
            ):
                return None

            return SelectionSetNode(
                selections=(
                    FieldNode(
                        name=NameNode(value="__typename"), arguments=(), directives=()
                    ),
                    *node.selections,
                )
            )

    result = visit(document, TypeInfoVisitor(type_info, _Visitor()))
    assert isinstance(result, DocumentNode)
    return result


def get_transitively_spread_fragments(
    node: Node, /, *, definitions: Mapping[str, FragmentDefinitionNode]
) -> list[FragmentDefinitionNode]:
    spread: set[str] = set()

    class _Visitor(Visitor):
        def enter_fragment_spread(
            self, spread_node: FragmentSpreadNode, *_args: object
        ) -> None:
            fragment_name = spread_node.name.value

            if fragment_name not in spread:
                spread.add(fragment_name)
                visit(definitions[fragment_name], self)

    visit(node, _Visitor())
    return [
        definition
        for fragment_name, definition in definitions.items()
        if fragment_name in spread
    ]

from dataclasses import replace
from functools import cache
from typing import Final

from graphql import (
    DocumentNode,
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    InlineFragmentNode,
    NameNode,
    Node,
    OperationDefinitionNode,
    OperationType,
    SelectionNode,
    SelectionSetNode,
    VariableNode,
    Visitor,
    print_ast,
    visit,
)

from graphql_codegen._generator._document import get_transitively_spread_fragments
from graphql_codegen.runtime._sigil import SIGIL

# NUL: no printed name holds it, and `print_ast()` escapes it in strings.
_PLACEHOLDER: Final = "\x00"
_ESCAPED_SIGIL: Final = "\\u" + f"{ord(SIGIL):04x}"


class _References(Visitor):
    def __init__(self) -> None:
        super().__init__()
        self.fragment_names: list[str] = []
        self.uses_variables = False

    def enter_fragment_spread(self, node: FragmentSpreadNode, *_args: object) -> None:
        self.fragment_names.append(node.name.value)

    def enter_variable(self, _node: VariableNode, *_args: object) -> None:
        self.uses_variables = True


def _references(node: Node, /) -> _References:
    references = _References()
    visit(node, references)
    return references


class _SuffixVariables(Visitor):
    def enter_variable(self, node: VariableNode, *_args: object) -> VariableNode:
        return VariableNode(name=NameNode(value=f"{node.name.value}{_PLACEHOLDER}"))


def print_merge_template(document: DocumentNode, /) -> str:
    """A subscription selects one root field, and an operation with directives would apply them to the whole merge: neither gets a sigil.

    A fragment spread at the root is inlined, since its fields are root fields and need aliasing.
    So is a fragment using variables, since each operation of a merge suffixes its variables differently.
    """
    (operation,) = (
        definition
        for definition in document.definitions
        if isinstance(definition, OperationDefinitionNode)
    )

    if operation.operation is OperationType.SUBSCRIPTION or bool(operation.directives):
        return print_ast(document).replace(SIGIL, _ESCAPED_SIGIL)

    fragments = {
        definition.name.value: definition
        for definition in document.definitions
        if isinstance(definition, FragmentDefinitionNode)
    }

    @cache
    def uses_variables(fragment_name: str, /) -> bool:
        references = _references(fragments[fragment_name])
        return references.uses_variables or any(
            map(uses_variables, references.fragment_names)
        )

    def rewrite(selection_set: SelectionSetNode, /, *, root: bool) -> SelectionSetNode:
        selections: list[SelectionNode] = []

        for selection in selection_set.selections:
            match selection:
                case FragmentSpreadNode() if root or uses_variables(
                    selection.name.value
                ):
                    fragment = fragments[selection.name.value]
                    selections.append(
                        InlineFragmentNode(
                            type_condition=fragment.type_condition,
                            directives=selection.directives,
                            selection_set=rewrite(fragment.selection_set, root=root),
                        ),
                    )
                case FieldNode() if root:
                    selections.append(
                        replace(
                            selection,
                            alias=NameNode(
                                value=f"{(selection.alias or selection.name).value}{_PLACEHOLDER}"
                            ),
                            selection_set=None
                            if selection.selection_set is None
                            else rewrite(selection.selection_set, root=False),
                        ),
                    )
                case FieldNode(selection_set=SelectionSetNode() as field_selection_set):
                    selections.append(
                        replace(
                            selection,
                            selection_set=rewrite(field_selection_set, root=False),
                        )
                    )
                case InlineFragmentNode():
                    # The fields of an inline fragment at the root are root fields too.
                    selections.append(
                        replace(
                            selection,
                            selection_set=rewrite(selection.selection_set, root=root),
                        )
                    )
                case _:
                    selections.append(selection)

        return SelectionSetNode(selections=tuple(selections))

    templated = visit(
        replace(operation, selection_set=rewrite(operation.selection_set, root=True)),
        _SuffixVariables(),
    )
    text = print_ast(
        DocumentNode(
            definitions=(
                templated,
                *get_transitively_spread_fragments(templated, definitions=fragments),
            )
        )
    )
    return text.replace(SIGIL, _ESCAPED_SIGIL).replace(_PLACEHOLDER, SIGIL)

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast, final

from graphql import (
    BooleanValueNode,
    DirectiveNode,
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLCompositeType,
    GraphQLIncludeDirective,
    GraphQLOutputType,
    GraphQLSchema,
    GraphQLSkipDirective,
    InlineFragmentNode,
    OperationDefinitionNode,
    SelectionSetNode,
    TypeInfo,
    is_type_sub_type_of,
    type_from_ast,
)


def inclusion(directives: Sequence[DirectiveNode], /) -> bool | None:
    """Whether `@skip` and `@include` include a selection, or ``None`` when a variable decides.

    graphql-core's :func:`~graphql.execution.collect_fields.should_include_node` needs the variables' values, which only exist at runtime.
    A directive without its `if` argument, which validation reports, decides nothing either.
    """
    included: bool | None = True

    for directive in directives:
        name = directive.name.value

        if name not in {GraphQLSkipDirective.name, GraphQLIncludeDirective.name}:
            continue

        value = next(
            (
                argument.value
                for argument in directive.arguments or ()
                if argument.name.value == "if"
            ),
            None,
        )

        if not isinstance(value, BooleanValueNode):
            included = None
        elif value.value == (name == GraphQLSkipDirective.name):
            return False

    return included


@final
@dataclass(frozen=True, kw_only=True)
class SelectedField:
    name: str
    alias: str | None
    type: GraphQLOutputType
    parent_type: GraphQLCompositeType
    directive_names: frozenset[str]
    description: str
    selection_set: "SelectionSet | None"
    skippable: bool

    @property
    def response_name(self) -> str:
        return self.alias or self.name


@final
@dataclass(frozen=True, kw_only=True)
class TypeCondition:
    type: GraphQLCompositeType
    selection_set: "SelectionSet"
    skippable: bool


@final
@dataclass(frozen=True, kw_only=True)
class SelectionSet:
    """A selection set, resolved, with the selections under a type condition kept apart.

    ``fields`` holds what is selected on every value, ``conditions`` what is selected only on some, and ``fragment_names`` the fragments spread here.
    All three are in document order, which keeps emission deterministic.
    Spreads are recorded rather than expanded, so that emission can reuse the fragment's own type as a base class.
    """

    type: GraphQLCompositeType
    fields: tuple[SelectedField, ...]
    conditions: tuple[TypeCondition, ...]
    fragment_names: tuple[str, ...]
    description: str


@final
class SelectionResolver:
    """Resolve the type of every field an operation or a fragment selects.

    This is the part a generator inferring types itself easily gets wrong, on `interface` chains notably, and graphql-core supplies all of it, so nothing here infers a type:

    - :class:`~graphql.TypeInfo` tracks, during a recursive descent calling its :meth:`~graphql.TypeInfo.enter` and :meth:`~graphql.TypeInfo.leave`, the definition of each field, meta fields included, and the type each selection set selects on;
    - :func:`~graphql.is_type_sub_type_of` tells whether a fragment's type condition holds for every value of a type.

    What is left is what graphql-core leaves to its caller: following fragment spreads, and `@skip` and `@include`, whose variables only have values at runtime.
    """

    def __init__(
        self,
        fragment_definitions: Mapping[str, FragmentDefinitionNode],
        /,
        *,
        schema: GraphQLSchema,
    ) -> None:
        self._fragment_definitions: Final = fragment_definitions
        self._schema: Final = schema

    def resolve(
        self, definition: OperationDefinitionNode | FragmentDefinitionNode, /
    ) -> SelectionSet:
        type_info = TypeInfo(self._schema)
        # Entering the definition pushes the operation's root type, or the fragment's type condition.
        type_info.enter(definition)
        selection_set = self._resolve_selection_set(
            definition.selection_set, type_info=type_info
        )
        type_info.leave(definition)
        return selection_set

    def _resolve_selection_set(
        self, node: SelectionSetNode, /, *, type_info: TypeInfo
    ) -> SelectionSet:
        # Entering it makes the type it selects on the parent type of what it selects.
        type_info.enter(node)
        parent_type = type_info.get_parent_type()
        assert parent_type is not None, (
            "Validation guarantees that a selection set selects on a composite type."
        )
        fields: list[SelectedField] = []
        conditions: list[TypeCondition] = []
        fragment_names: list[str] = []

        for selection in node.selections:
            # `None` rather than empty when the selection has none.
            included = inclusion(selection.directives or ())

            # Never in the response, so not in the type either.
            if included is False:
                continue

            skippable = included is None

            match selection:
                case FieldNode():
                    fields.append(
                        self._resolve_field(
                            selection, type_info=type_info, skippable=skippable
                        )
                    )
                case InlineFragmentNode():
                    type_info.enter(selection)
                    # Its type condition, or, without one, the enclosing type.
                    selection_set = self._resolve_selection_set(
                        selection.selection_set, type_info=type_info
                    )
                    type_info.leave(selection)
                    conditions.append(
                        TypeCondition(
                            type=selection_set.type,
                            selection_set=selection_set,
                            skippable=skippable,
                        )
                    )
                case FragmentSpreadNode():
                    # `TypeInfo` does not follow a spread: the fragment is resolved on its own, by name.
                    fragment_name = selection.name.value
                    fragment_type = cast(
                        "GraphQLCompositeType",
                        type_from_ast(
                            self._schema,
                            self._fragment_definitions[fragment_name].type_condition,
                        ),
                    )

                    if not skippable and is_type_sub_type_of(
                        self._schema, parent_type, fragment_type
                    ):
                        # Its type condition holds for every value selected here, so the fragment's type stands in, as a base class.
                        fragment_names.append(fragment_name)
                    else:
                        # One on a narrower type is a branch, as an inline fragment is, and so is a skippable one, since standing in would declare its keys present.
                        conditions.append(
                            TypeCondition(
                                type=fragment_type,
                                selection_set=SelectionSet(
                                    type=fragment_type,
                                    fragment_names=(fragment_name,),
                                    fields=(),
                                    conditions=(),
                                    description="",
                                ),
                                skippable=skippable,
                            ),
                        )
                case _:  # pragma: no cover
                    raise TypeError(f"Unexpected selection: {selection}")

        type_info.leave(node)
        return SelectionSet(
            type=parent_type,
            fields=tuple(fields),
            conditions=tuple(conditions),
            fragment_names=tuple(fragment_names),
            description=parent_type.description or "",
        )

    def _resolve_field(
        self, node: FieldNode, /, *, type_info: TypeInfo, skippable: bool
    ) -> SelectedField:
        type_info.enter(node)
        parent_type = type_info.get_parent_type()
        # `__typename` included: `TypeInfo` knows the meta fields.
        field_definition = type_info.get_field_def()
        assert parent_type is not None and field_definition is not None, (
            "Validation guarantees that a selected field exists."
        )
        selection_set = (
            None
            if node.selection_set is None
            else self._resolve_selection_set(node.selection_set, type_info=type_info)
        )
        type_info.leave(node)
        return SelectedField(
            name=node.name.value,
            alias=node.alias.value if node.alias else None,
            type=field_definition.type,
            parent_type=parent_type,
            directive_names=frozenset(
                directive.name.value for directive in node.directives or ()
            ),
            description=field_definition.description or "",
            selection_set=selection_set,
            skippable=skippable,
        )

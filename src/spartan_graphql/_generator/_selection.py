from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, assert_never, cast, final

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

type Inclusion = Literal["always", "never", "runtime"]
"""Whether `@skip` and `@include` keep a selection, or leave it to a variable at runtime, which graphql-core's `should_include_node()` cannot tell without values."""


def inclusion(directives: Sequence[DirectiveNode], /) -> Inclusion:
    result: Inclusion = "always"

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
            result = "runtime"
        elif value.value == (name == GraphQLSkipDirective.name):
            return "never"

    return result


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
    """`fields` is selected on every value, `conditions` only on some, and `fragment_names` are the spreads, kept for base classes."""

    type: GraphQLCompositeType
    fields: tuple[SelectedField, ...]
    conditions: tuple[TypeCondition, ...]
    fragment_names: tuple[str, ...]
    description: str


@final
class SelectionResolver:
    """Nothing here infers a type: graphql-core's `TypeInfo` tracks each field's definition, and `is_type_sub_type_of()` tells whether a type condition holds for every value."""

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
        type_info.enter(definition)
        selection_set = self._resolve_selection_set(
            definition.selection_set, type_info=type_info
        )
        type_info.leave(definition)
        return selection_set

    def _resolve_selection_set(
        self, node: SelectionSetNode, /, *, type_info: TypeInfo
    ) -> SelectionSet:
        type_info.enter(node)
        parent_type = type_info.get_parent_type()
        assert parent_type is not None, (
            "Validation guarantees that a selection set selects on a composite type."
        )
        fields: list[SelectedField] = []
        conditions: list[TypeCondition] = []
        fragment_names: list[str] = []

        for selection in node.selections:
            match inclusion(selection.directives or ()):
                case "never":
                    continue
                case "always":
                    skippable = False
                case "runtime":
                    skippable = True
                case _ as never:
                    assert_never(never)

            match selection:
                case FieldNode():
                    fields.append(
                        self._resolve_field(
                            selection, type_info=type_info, skippable=skippable
                        )
                    )
                case InlineFragmentNode():
                    type_info.enter(selection)
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
                    # `TypeInfo` does not follow a spread.
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
                        # Holds for every value here, so the fragment's type is a base class.
                        fragment_names.append(fragment_name)
                    else:
                        # A narrower or skippable one is a branch, since a base would declare its keys present.
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

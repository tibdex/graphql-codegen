import ast
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache
from typing import Final, final

from graphql import (
    GraphQLCompositeType,
    GraphQLObjectType,
    GraphQLSchema,
    is_non_null_type,
    is_type_sub_type_of,
)

from graphql_codegen._generator._annotation import AnnotationBuilder
from graphql_codegen._generator._ast_nodes import (
    constant,
    documented,
    name,
    optional,
    qualified,
    subscript,
    type_alias,
    union,
)
from graphql_codegen._generator._naming import (
    COMPAT,
    INPUT,
    REFLECTION,
    TYPING,
    SchemaSpellings,
    nested_type_name,
)
from graphql_codegen._generator._schema import as_non_null
from graphql_codegen._generator._selection import (
    SelectedField,
    SelectionSet,
    TypeCondition,
)
from graphql_codegen._generator._structs import StructTypes, get_struct_input_name
from graphql_codegen._generator._typed_dict import Key, emit_data
from graphql_codegen._generator.spelling import (
    PendingSpelling,
    SettledSpelling,
    Spelling,
)


@final
@dataclass(frozen=True)
class _DeclaredKey:
    field: SelectedField
    declaring_fragment: str


def _merge_selection_sets(
    selection_sets: Sequence[SelectionSet], /, *, type_: GraphQLCompositeType
) -> SelectionSet:
    return SelectionSet(
        type=type_,
        fields=tuple(
            field for selection_set in selection_sets for field in selection_set.fields
        ),
        conditions=tuple(
            condition
            for selection_set in selection_sets
            for condition in selection_set.conditions
        ),
        fragment_names=tuple(
            dict.fromkeys(
                fragment_name
                for selection_set in selection_sets
                for fragment_name in selection_set.fragment_names
            ),
        ),
        description=next(
            (
                selection_set.description
                for selection_set in selection_sets
                if selection_set.description
            ),
            "",
        ),
    )


def _merge_fields(fields: Sequence[SelectedField], /) -> tuple[SelectedField, ...]:
    """Validation guarantees that fields sharing a response name are the same field with the same arguments, so only their selection sets and client directives need combining."""
    by_response_name: dict[str, list[SelectedField]] = {}

    for field in fields:
        by_response_name.setdefault(field.response_name, []).append(field)

    return tuple(
        group[0] if len(group) == 1 else _merge_field_group(group)
        for group in by_response_name.values()
    )


def _merge_field_group(group: Sequence[SelectedField], /) -> SelectedField:
    # Validation guarantees that the fields are all leaves or all select on the same type.
    selection_sets = [
        field.selection_set for field in group if field.selection_set is not None
    ]
    return replace(
        group[0],
        directive_names=frozenset().union(*(field.directive_names for field in group)),
        selection_set=_merge_selection_sets(
            selection_sets, type_=selection_sets[0].type
        )
        if selection_sets
        else None,
        # Present as soon as one of the fields is.
        skippable=all(field.skippable for field in group),
    )


@final
class DataTypeEmitter:
    """Turn resolved selection sets into TypedDicts, in class syntax, so that a fragment spread can be a base class.

    Inheritance is what keeps a type name stable: adding a field next to a spread changes the class body, never the name.
    A fragment's own class is open, since an operation spreading it extends it, and every other type is closed, since a response holds exactly the keys its operation selects.
    Types are emitted depth first, children before parents, so a name is always defined before it is used as a base class.
    One emitter serves a whole package, so that each fragment is normalized once, whichever modules spread it.
    """

    def __init__(
        self,
        *,
        schema: GraphQLSchema,
        annotations: AnnotationBuilder,
        schema_spellings: SchemaSpellings,
        resolve_fragment: Callable[[str], SelectionSet],
        struct_types: StructTypes | None,
        non_null_directive_name: str | None,
    ) -> None:
        self._schema: Final = schema
        self._annotations: Final = annotations
        self._schema_spellings: Final = schema_spellings
        self._resolve_fragment: Final = resolve_fragment
        self._struct_types: Final = struct_types
        self._non_null_directive_name: Final = non_null_directive_name
        # Cached for the emitter's life: one serves a whole package.
        self._normalized_fragment: Final = cache(self._normalize_fragment)
        self._declared_keys: Final = cache(self._find_declared_keys)

    def emit(
        self,
        selection_set: SelectionSet,
        /,
        *,
        root_spelling: SettledSpelling,
        fragment_reference: Callable[[str], ast.expr],
        inheritable: bool,
    ) -> list[ast.stmt]:
        """The outermost type is public and every nested one private, its spelling pending, so that a path spelled like another gets a suffix rather than clashing."""
        return self._emit_node(
            selection_set,
            spelling=root_spelling,
            path=(root_spelling,),
            fragment_reference=fragment_reference,
            inheritable=inheritable,
        )

    def _emit_node(
        self,
        selection_set: SelectionSet,
        /,
        *,
        spelling: Spelling,
        path: Sequence[str],
        fragment_reference: Callable[[str], ast.expr],
        inheritable: bool,
    ) -> list[ast.stmt]:
        """A class's keys are required, since GraphQL guarantees that a selected field is present in the response, unless `@skip` or `@include` may leave it out, nullability living in the annotation."""
        return self._emit_normalized(
            self._normalize(selection_set),
            spelling=spelling,
            path=path,
            fragment_reference=fragment_reference,
            inheritable=inheritable,
        )

    def _emit_normalized(
        self,
        normalized: SelectionSet,
        /,
        *,
        spelling: Spelling,
        path: Sequence[str],
        fragment_reference: Callable[[str], ast.expr],
        inheritable: bool,
    ) -> list[ast.stmt]:
        if normalized.conditions:
            return self._emit_union(
                normalized,
                spelling=spelling,
                path=path,
                fragment_reference=fragment_reference,
            )

        statements: list[ast.stmt] = []
        keys: list[Key] = []

        for field in normalized.fields:
            if field.name == "__typename":
                keys.append(
                    Key(
                        name="__typename",
                        annotation=_typename_annotation(
                            self._possible_types(normalized.type)
                        ),
                        required=not field.skippable,
                        description="",
                    ),
                )
                continue

            child_statements, annotation = self._emit_field(
                field, path=path, fragment_reference=fragment_reference
            )
            statements.extend(child_statements)
            keys.append(
                Key(
                    name=field.response_name,
                    annotation=annotation,
                    required=not field.skippable,
                    description=field.description,
                ),
            )

        statements.extend(
            emit_data(
                spelling,
                keys,
                bases=[
                    fragment_reference(fragment_name)
                    for fragment_name in normalized.fragment_names
                ],
                closed=not inheritable,
                functional_base_spelling=PendingSpelling(
                    f"{nested_type_name(path)}_Base"
                ),
                description=normalized.description,
            ),
        )
        return statements

    def _emit_union(
        self,
        selection_set: SelectionSet,
        /,
        *,
        spelling: Spelling,
        path: Sequence[str],
        fragment_reference: Callable[[str], ast.expr],
    ) -> list[ast.stmt]:
        """Emit *selection_set*, which has conditions, as a union with one member per concrete type, discriminated by `__typename`, which the document selects on every abstract type.

        Each member gathers the fields of every condition its type satisfies, so conditions nested in conditions need no case of their own.
        """
        statements: list[ast.stmt] = []
        members: list[PendingSpelling] = []

        for object_type in self._possible_types(selection_set.type):
            member_path = (*path, object_type.name)
            member = PendingSpelling(nested_type_name(member_path))
            members.append(member)
            statements.extend(
                self._emit_node(
                    self._selection_on(selection_set, object_type),
                    spelling=member,
                    path=member_path,
                    fragment_reference=fragment_reference,
                    inheritable=False,
                ),
            )

        statements.extend(
            documented(
                type_alias(spelling, union(*(name(member) for member in members))),
                text=selection_set.description,
            ),
        )
        return statements

    def _selection_on(
        self, selection_set: SelectionSet, object_type: GraphQLObjectType, /
    ) -> SelectionSet:
        gathered = [selection_set]

        for condition in selection_set.conditions:
            if is_type_sub_type_of(self._schema, object_type, condition.type):
                branch = self._selection_on(condition.selection_set, object_type)
                gathered.append(
                    self._as_skippable(branch, object_type=object_type)
                    if condition.skippable
                    else branch
                )

        merged = _merge_selection_sets(
            [replace(part, conditions=()) for part in gathered], type_=object_type
        )
        return replace(merged, description=object_type.description or "")

    def _as_skippable(
        self, selection_set: SelectionSet, /, *, object_type: GraphQLObjectType
    ) -> SelectionSet:
        """Its fragments are expanded, since inheriting one would declare its keys present."""
        fields = [replace(field, skippable=True) for field in selection_set.fields]

        for fragment_name in selection_set.fragment_names:
            fragment = self._selection_on(
                self._resolve_fragment(fragment_name), object_type
            )
            fields.extend(self._as_skippable(fragment, object_type=object_type).fields)

        return replace(selection_set, fields=tuple(fields), fragment_names=())

    def _normalize(self, selection_set: SelectionSet, /) -> SelectionSet:
        """Merge fields by key, and expand every spread that cannot soundly be inherited, which is always correct, merely less reused.

        A fragment can soundly be inherited when its own type is a class rather than a union, and every key it shares with the rest of the node is the very same one, declared by the same fragment or as the same leaf.
        """
        fields = list(selection_set.fields)
        conditions: list[TypeCondition] = list(selection_set.conditions)
        bases: list[str] = []
        expanded: set[str] = set()
        pending = list(dict.fromkeys(selection_set.fragment_names))

        def expand(fragment_name: str, /) -> None:
            expanded.add(fragment_name)
            fragment = self._resolve_fragment(fragment_name)
            fields.extend(fragment.fields)
            conditions.extend(fragment.conditions)
            pending.extend(fragment.fragment_names)

        while True:
            while pending:
                fragment_name = pending.pop(0)

                if fragment_name in bases or fragment_name in expanded:
                    continue

                if self._normalized_fragment(fragment_name).conditions:
                    # A fragment whose type is a union cannot be a base.
                    expand(fragment_name)
                else:
                    bases.append(fragment_name)

            merged = _merge_fields(fields)
            conflicting = next(
                (
                    base
                    for base in bases
                    if self._declares_a_key_otherwise(
                        base,
                        fields=merged,
                        other_bases=[other for other in bases if other != base],
                    )
                ),
                None,
            )

            if conflicting is None:
                return replace(
                    selection_set,
                    fields=merged,
                    conditions=tuple(conditions),
                    fragment_names=tuple(bases),
                )

            bases.remove(conflicting)
            expand(conflicting)

    def _normalize_fragment(self, fragment_name: str, /) -> SelectionSet:
        return self._normalize(self._resolve_fragment(fragment_name))

    def _find_declared_keys(self, fragment_name: str, /) -> Mapping[str, _DeclaredKey]:
        normalized = self._normalized_fragment(fragment_name)
        declared: dict[str, _DeclaredKey] = {}

        for base in normalized.fragment_names:
            declared.update(self._declared_keys(base))

        for field in normalized.fields:
            declared[field.response_name] = _DeclaredKey(
                field=field, declaring_fragment=fragment_name
            )

        return declared

    def _declares_a_key_otherwise(
        self,
        base: str,
        /,
        *,
        fields: Sequence[SelectedField],
        other_bases: Sequence[str],
    ) -> bool:
        own = {field.response_name: field for field in fields}

        for key, declared in self._declared_keys(base).items():
            if key in own and not self._same_leaf(declared.field, own[key]):
                return True

            for other in other_bases:
                other_declared = self._declared_keys(other).get(key)

                if (
                    other_declared is not None
                    and other_declared.declaring_fragment != declared.declaring_fragment
                    and not self._same_leaf(declared.field, other_declared.field)
                ):
                    return True

        return False

    def _same_leaf(self, field: SelectedField, other: SelectedField, /) -> bool:
        """Whether two fields get the very same annotation, which only leaves can."""
        return (
            field.selection_set is None
            and other.selection_set is None
            and self._leaf_signature(field) == self._leaf_signature(other)
        )

    def _leaf_signature(self, field: SelectedField, /) -> tuple[object, ...]:
        return (
            field.name,
            str(field.type),
            self._is_non_null(field),
            self._struct_input_name(field),
            field.skippable,
        )

    def _struct_input_name(self, field: SelectedField, /) -> str | None:
        return get_struct_input_name(
            field.parent_type,
            field.name,
            struct_types=self._struct_types,
        )

    def _is_non_null(self, field: SelectedField, /) -> bool:
        return self._non_null_directive_name in field.directive_names

    def _emit_field(
        self,
        field: SelectedField,
        /,
        *,
        path: Sequence[str],
        fragment_reference: Callable[[str], ast.expr],
    ) -> tuple[list[ast.stmt], ast.expr]:
        non_null = self._is_non_null(field)
        # `@nonNull` is the caller asserting that this query cannot return null here, so the annotation loses its `| None`, and `NON_NULL` tells the runtime to enforce it.
        field_type = as_non_null(field.type) if non_null else field.type
        statements: list[ast.stmt] = []
        struct_input_name = self._struct_input_name(field)

        if struct_input_name is not None:
            annotation: ast.expr = qualified(
                INPUT,
                self._schema_spellings.type_spellings[struct_input_name],
            )

            if not is_non_null_type(field_type):
                annotation = optional(annotation)
        elif field.selection_set is None:
            annotation = self._annotations.build(
                field_type,
                position="data",
                object_annotation=None,
                bare_inputs=False,
            )
        else:
            normalized = self._normalize(field.selection_set)
            object_annotation: ast.expr

            match normalized:
                case SelectionSet(
                    fields=(), conditions=(), fragment_names=(fragment_name,)
                ):
                    # The fragment's type itself, rather than a class adding nothing to it.
                    object_annotation = fragment_reference(fragment_name)
                case _:
                    child_path = (*path, field.response_name)
                    child = PendingSpelling(nested_type_name(child_path))
                    statements = self._emit_normalized(
                        normalized,
                        spelling=child,
                        path=child_path,
                        fragment_reference=fragment_reference,
                        inheritable=False,
                    )
                    object_annotation = name(child)

            annotation = self._annotations.build(
                field_type,
                position="data",
                object_annotation=object_annotation,
                bare_inputs=False,
            )

        if non_null:
            annotation = subscript(
                qualified(TYPING, "Annotated"),
                annotation,
                qualified(REFLECTION, "NON_NULL"),
            )

        return statements, annotation

    def _possible_types(
        self, type_: GraphQLCompositeType, /
    ) -> Sequence[GraphQLObjectType]:
        return (
            (type_,)
            if isinstance(type_, GraphQLObjectType)
            else self._schema.get_possible_types(type_)
        )


def _typename_annotation(possible_types: Sequence[GraphQLObjectType], /) -> ast.expr:
    # Read-only, which is what lets a subclass narrow the `__typename` of a fragment it inherits: a mutable key cannot change type in a subclass.
    return subscript(
        qualified(COMPAT, "ReadOnly"),
        subscript(
            qualified(TYPING, "Literal"),
            *(constant(possible_type.name) for possible_type in possible_types),
        ),
    )

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from typing import final

from graphql_codegen._generator._ast_nodes import (
    class_definition,
    constant,
    documented,
    name,
    qualified,
    subscript,
)
from graphql_codegen._generator._naming import COMPAT, TYPING
from graphql_codegen._generator.spelling import Spelling, ast_str, is_bindable


@final
@dataclass(frozen=True, kw_only=True)
class Key:
    name: str
    annotation: ast.expr
    required: bool
    description: str


def _functional(
    type_spelling: Spelling, keys: Sequence[Key], /, *, closed: bool
) -> ast.Assign:
    # The variable is named after the name argument.
    # Both are the same placeholder, so they settle to the same spelling.
    return ast.Assign(
        targets=[ast.Name(id=ast_str(type_spelling), ctx=ast.Store())],
        value=ast.Call(
            func=qualified(COMPAT, "TypedDict"),
            args=[
                constant(ast_str(type_spelling)),
                ast.Dict(
                    keys=[constant(key.name) for key in keys],
                    values=[_annotation(key) for key in keys],
                ),
            ],
            keywords=[ast.keyword(arg="closed", value=ast.Constant(value=True))]
            if closed
            else [],
        ),
    )


def _can_be_declared_in_a_class_body(
    key: str, /, *, bare_names: frozenset[str]
) -> bool:
    return is_bindable(key) and not key.startswith("__") and key not in bare_names


def _annotation(key: Key, /) -> ast.expr:
    return (
        key.annotation
        if key.required
        else subscript(qualified(TYPING, "NotRequired"), key.annotation)
    )


def _declaration(key: Key, /) -> list[ast.stmt]:
    return documented(
        ast.AnnAssign(
            target=ast.Name(id=key.name, ctx=ast.Store()),
            annotation=_annotation(key),
            simple=1,
        ),
        text=key.description,
    )


def emit_closed(
    type_spelling: Spelling,
    keys: Sequence[Key],
    /,
    *,
    description: str,
    bare_names: frozenset[str],
) -> list[ast.stmt]:
    if all(
        _can_be_declared_in_a_class_body(key.name, bare_names=bare_names)
        for key in keys
    ):
        return [
            class_definition(
                type_spelling,
                bases=[qualified(COMPAT, "TypedDict")],
                keywords={"closed": ast.Constant(value=True)},
                body=[statement for key in keys for statement in _declaration(key)],
                docstring_text=description,
            ),
        ]

    return documented(_functional(type_spelling, keys, closed=True), text=description)


def emit_data(
    type_spelling: Spelling,
    keys: Sequence[Key],
    /,
    *,
    bases: Sequence[ast.expr],
    closed: bool,
    functional_base_spelling: Spelling,
    description: str,
) -> list[ast.stmt]:
    # A data module references no public name unqualified.
    body_keys = [
        key
        for key in keys
        if _can_be_declared_in_a_class_body(key.name, bare_names=frozenset())
    ]
    base_keys = [key for key in keys if key not in body_keys]
    statements: list[ast.stmt] = []
    all_bases = list(bases)

    if base_keys:
        statements.append(
            _functional(functional_base_spelling, base_keys, closed=False),
        )
        all_bases.insert(0, name(functional_base_spelling))

    statements.append(
        class_definition(
            type_spelling,
            bases=all_bases or [qualified(COMPAT, "TypedDict")],
            body=[statement for key in body_keys for statement in _declaration(key)],
            docstring_text=description,
            keywords={"closed": ast.Constant(value=True)} if closed else {},
        ),
    )
    return statements

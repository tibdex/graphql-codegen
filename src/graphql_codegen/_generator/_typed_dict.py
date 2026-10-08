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
    """The string passed as the first argument must match the variable it is assigned to, or `ty` reports `mismatched-type-name`."""
    return ast.Assign(
        targets=[ast.Name(id=ast_str(type_spelling), ctx=ast.Store())],
        value=ast.Call(
            func=qualified(COMPAT, "TypedDict"),
            args=[
                # Spelled like the target, placeholder included, so both get the same name.
                constant(ast_str(type_spelling)),
                ast.Dict(
                    keys=[constant(key.name) for key in keys],
                    values=[_annotation(key, closed=closed) for key in keys],
                ),
            ],
            keywords=[ast.keyword(arg="closed", value=ast.Constant(value=True))]
            if closed
            else [],
        ),
    )


def _is_class_attribute_safe(key: str, /, *, bare_names: frozenset[str]) -> bool:
    """A name Python cannot bind cannot be, a name starting with `__` would be mangled, and a public name the module references unqualified would be shadowed by the key in the class scope.

    No name the generator adds can clash with a key, since :func:`~graphql_codegen._spelling.settle_module` settles none like any name the module holds.
    """
    return is_bindable(key) and not key.startswith("__") and key not in bare_names


def _annotation(key: Key, /, *, closed: bool) -> ast.expr:
    """The key's annotation, saying whether it must be present: explicitly in a closed TypedDict, where reading what a caller must send matters, and only when it may be absent in an open one."""
    if closed:
        return subscript(
            qualified(TYPING, "Required" if key.required else "NotRequired"),
            key.annotation,
        )

    return (
        key.annotation
        if key.required
        else subscript(qualified(TYPING, "NotRequired"), key.annotation)
    )


def _declaration(key: Key, /, *, closed: bool) -> list[ast.stmt]:
    return documented(
        ast.AnnAssign(
            target=ast.Name(id=key.name, ctx=ast.Store()),
            annotation=_annotation(key, closed=closed),
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
    """Emit a closed TypedDict, as an input or variables type is, in class syntax unless a key cannot be declared in a class body, and then in functional syntax altogether, since a closed TypedDict cannot be extended with keys by a subclass."""
    if all(_is_class_attribute_safe(key.name, bare_names=bare_names) for key in keys):
        return [
            class_definition(
                type_spelling,
                bases=[qualified(COMPAT, "TypedDict")],
                keywords={"closed": ast.Constant(value=True)},
                body=[
                    statement
                    for key in keys
                    for statement in _declaration(key, closed=True)
                ],
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
    """Emit a data type in class syntax, inheriting the keys a class body cannot declare from a functional base, so that it can still inherit fragments too.

    The functional base is open, since the class extends it.
    """
    # A data module references no public name unqualified.
    safe = [
        key
        for key in keys
        if _is_class_attribute_safe(key.name, bare_names=frozenset())
    ]
    unsafe = [key for key in keys if key not in safe]
    statements: list[ast.stmt] = []
    all_bases = list(bases)

    if unsafe:
        statements.append(
            _functional(functional_base_spelling, unsafe, closed=False),
        )
        all_bases.insert(0, name(functional_base_spelling))

    statements.append(
        class_definition(
            type_spelling,
            bases=all_bases or [qualified(COMPAT, "TypedDict")],
            body=[
                statement
                for key in safe
                for statement in _declaration(key, closed=False)
            ],
            docstring_text=description,
            keywords={"closed": ast.Constant(value=True)} if closed else {},
        ),
    )
    return statements

import ast
from ast import literal_eval
from collections.abc import Mapping, Sequence
from io import StringIO
from itertools import accumulate, pairwise
from textwrap import indent
from tokenize import NEWLINE, STRING, generate_tokens
from types import EllipsisType
from typing import Final

from graphql_codegen._generator.spelling import Spelling, ast_str


def name(spelling: Spelling, /) -> ast.Name:
    return ast.Name(id=ast_str(spelling), ctx=ast.Load())


def qualified(module_alias: Spelling, attribute: str, /) -> ast.Attribute:
    """Every name the module did not declare is qualified, builtins included, so that an operation named `str` cannot hide `_builtins.str`."""
    return ast.Attribute(value=name(module_alias), attr=attribute, ctx=ast.Load())


def import_as(module_name: str, alias: Spelling, /) -> ast.Import:
    return ast.Import(names=[ast.alias(name=module_name, asname=ast_str(alias))])


def constant(value: str | int | EllipsisType | None, /) -> ast.Constant:
    return ast.Constant(value=value)


FUTURE_ANNOTATIONS: Final = ast.ImportFrom(
    module="__future__",
    names=[ast.alias(name="annotations")],
    level=0,
)
"""Input types reference each other in cycles, so the schema module postpones its annotations instead of quoting them.

Under PEP 563, a TypedDict's `__required_keys__` then reports every key as required: read these types with `typing.get_type_hints()`.
"""


def subscript(value: ast.expr, *items: ast.expr) -> ast.Subscript:
    index: ast.expr = (
        items[0] if len(items) == 1 else ast.Tuple(elts=list(items), ctx=ast.Load())
    )
    return ast.Subscript(value=value, slice=index, ctx=ast.Load())


def union(*options: ast.expr) -> ast.expr:
    assert options, "A union needs at least one option."
    result = options[0]

    for option in options[1:]:
        result = ast.BinOp(left=result, op=ast.BitOr(), right=option)

    return result


def optional(annotation: ast.expr, /) -> ast.expr:
    return union(annotation, constant(None))


def type_alias(target: Spelling, value: ast.expr, /) -> ast.TypeAlias:
    """A `type` statement evaluates lazily, so it may name a type declared after it."""
    return ast.TypeAlias(
        name=ast.Name(id=ast_str(target), ctx=ast.Store()),
        type_params=[],
        value=value,
    )


def documented(statement: ast.stmt, /, *, text: str) -> list[ast.stmt]:
    """A `type` statement and an annotated attribute have no docstring, but tooling reads a bare string right below them."""
    return [statement, docstring(text)] if text else [statement]


def docstring(text: str, /) -> ast.Expr:
    """`ast.unparse()` drops comments, so a docstring is the only text that reaches the generated code."""
    return ast.Expr(value=constant(text))


def class_definition(
    class_spelling: Spelling,
    /,
    *,
    bases: Sequence[ast.expr],
    keywords: Mapping[str, ast.expr],
    body: Sequence[ast.stmt],
    docstring_text: str,
) -> ast.ClassDef:
    statements: list[ast.stmt] = list(body)

    if docstring_text:
        statements.insert(0, docstring(docstring_text))

    if not statements:
        statements.append(ast.Expr(value=constant(Ellipsis)))

    return ast.ClassDef(
        name=ast_str(class_spelling),
        bases=list(bases),
        keywords=[
            ast.keyword(arg=keyword, value=value) for keyword, value in keywords.items()
        ],
        body=statements,
        decorator_list=[],
        type_params=[],
    )


def module(body: Sequence[ast.stmt], /) -> ast.Module:
    return ast.Module(body=list(body), type_ignores=[])


def _string_literal(value: str, /, *, quotes: Sequence[str]) -> str:
    escaped = "".join(
        character
        if character in "\n\t" or (character.isprintable() and character != "\\")
        else character.encode("unicode_escape").decode()
        for character in value
    )
    usable = [quote for quote in quotes if quote not in escaped]

    if not usable:
        return repr(value)

    # Prefer a quote the value does not end with.
    quote = min(usable, key=lambda quote: escaped.endswith(quote[0]))

    if escaped.endswith(quote[0]):
        escaped = f"{escaped[:-1]}\\{escaped[-1]}"

    return f"{quote}{escaped}{quote}"


def _triple_quote(source: str, /) -> str:
    """`ast.unparse()` only triple quotes docstrings: elsewhere, a GraphQL document would become one line with escaped newlines.

    A bare string's continuation lines are indented like a handwritten docstring, which `inspect.cleandoc()` undoes.
    """
    line_starts = list(accumulate(map(len, StringIO(source)), initial=0))
    pieces: list[str] = []
    end = 0

    for token, following in pairwise(generate_tokens(StringIO(source).readline)):
        if token.type != STRING:
            continue

        value = literal_eval(token.string)
        row, column = token.start

        # A bare string statement, which `ast.unparse()` leaves unindented.
        if following.type == NEWLINE and not token.line[:column].strip():
            margin = " " * column
            literal = _string_literal(
                indent(value, margin).removeprefix(margin), quotes=['"""']
            )
        elif "\n" in value and not token.string.startswith(('"""', "'''")):
            literal = _string_literal(value, quotes=['"""', "'''"])
        else:
            continue

        start = line_starts[row - 1] + column
        pieces += [source[end:start], literal]
        end = line_starts[token.end[0] - 1] + token.end[1]

    return "".join([*pieces, source[end:]])


def unparse(tree: ast.Module, /) -> str:
    """Generated code is built as a tree, never from strings.

    The standard library has no formatter, so long lines are left to the project's own.
    """
    ast.fix_missing_locations(tree)

    chunks: list[str] = []
    previous_was_import = False

    for statement in tree.body:
        is_import = isinstance(statement, ast.Import | ast.ImportFrom)
        chunk = ast.unparse(statement)

        # A bare string stays glued to the statement it documents.
        is_docstring = isinstance(statement, ast.Expr) and isinstance(
            statement.value,
            ast.Constant,
        )

        if chunks and not (is_import and previous_was_import) and not is_docstring:
            chunks.append("")

        chunks.append(chunk)
        previous_was_import = is_import

    source = _triple_quote("\n".join(chunks))
    compile(source, "<generated>", "exec")
    return f"{source}\n"

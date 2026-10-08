import ast
from ast import literal_eval
from collections.abc import Iterable, Mapping, Sequence
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
    """`_alias.attribute`: how a generated module refers to anything it did not declare.

    Qualifying every such reference, builtins included, is what keeps a generated name from ever shadowing one the module uses: an operation called `str` cannot hide `_builtins.str`.
    """
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
"""Emitted by the input module, whose types reference each other in cycles, such as a filter combining filters, so that annotations need no quoting.

The cost: under PEP 563, a TypedDict's :attr:`~typing.TypedDict.__required_keys__` is computed from unresolved strings and reports every key as required.
Nothing here reads it, since nothing is validated at runtime, and type checkers, which read the source, are unaffected.
Code introspecting these types at runtime must use :func:`typing.get_type_hints` rather than the class attributes.
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
    """Preferred over `X: TypeAlias = ...`, since a PEP 695 alias evaluates its value lazily, so it may name types declared after it."""
    return ast.TypeAlias(
        name=ast.Name(id=ast_str(target), ctx=ast.Store()),
        type_params=[],
        value=value,
    )


def documented(statement: ast.stmt, /, *, text: str) -> list[ast.stmt]:
    """A `type` statement and an annotated attribute have no docstring slot in the grammar, but tooling widely understands a bare string literal directly underneath."""
    return [statement, docstring(text)] if text else [statement]


def docstring(text: str, /) -> ast.Expr:
    """A docstring holding *text*: :func:`ast.unparse` drops comments, so whatever the generated code tells its reader must be a docstring, which is a real node."""
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


def relative_import_from(
    module: str | None,
    names: Iterable[str],
    /,
    *,
    level: int,
    re_export: bool,
) -> ast.ImportFrom:
    """When *re_export* is ``True``, each name is aliased to itself, as in `from .X import X as X`, which is how PEP 484 marks an explicit re-export.

    Otherwise, a type checker treats the import as private to the module and rejects importing it from the package.
    """
    return ast.ImportFrom(
        module=module,
        names=[
            ast.alias(name=imported, asname=imported if re_export else None)
            for imported in names
        ],
        level=level,
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

    # A quote ending like the value would need that last character escaped.
    quote = min(usable, key=lambda quote: escaped.endswith(quote[0]))

    if escaped.endswith(quote[0]):
        escaped = f"{escaped[:-1]}\\{escaped[-1]}"

    return f"{quote}{escaped}{quote}"


def _triple_quote(source: str, /) -> str:
    """Respell with triple quotes the bare strings, which document the statement above them, and the strings spanning lines.

    :func:`ast.unparse` only does this in docstring position; everywhere else a newline becomes a backslash-n inside a single line literal.
    GraphQL documents are the only multi-line strings this library emits, and a triple quoted literal keeps them readable.

    The continuation lines of a bare string are indented to match, as a hand written docstring would be.
    That adds leading whitespace to the value, which is what :func:`inspect.cleandoc` exists to remove and what every tool rendering a docstring already does.
    """
    # Split as the tokenizer reads it.
    line_starts = list(accumulate(map(len, StringIO(source)), initial=0))
    pieces: list[str] = []
    end = 0

    for token, following in pairwise(generate_tokens(StringIO(source).readline)):
        if token.type != STRING:
            continue

        value = literal_eval(token.string)
        row, column = token.start

        # A docstring too, which `ast.unparse()` quotes but leaves unindented.
        if following.type == NEWLINE and not token.line[:column].strip():
            # Every line but the first, which follows the opening quotes.
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
    """Checked to compile, so that a bad node fails here rather than downstream.

    Generated code is always built as a tree and unparsed, never assembled from strings, which can be syntactically wrong and need forward references quoted by hand.
    :func:`ast.unparse` has no formatting options, such as line wrapping or blank lines, and the standard library has no formatter.
    Unparsing each top-level statement separately and grouping them still gives imports as a block and one blank line between declarations.
    Wrapping long lines is left to whatever formatter the consuming project runs; this library depends on none.

    """
    ast.fix_missing_locations(tree)

    chunks: list[str] = []
    previous_was_import = False

    for statement in tree.body:
        is_import = isinstance(statement, ast.Import | ast.ImportFrom)
        chunk = ast.unparse(statement)

        # A bare string documents the statement above it, so it must stay glued to it.
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

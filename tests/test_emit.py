import ast
import json
import shutil
import subprocess
import sys
from collections.abc import Mapping
from importlib.util import find_spec
from pathlib import Path, PurePosixPath

import pytest
from graphql import GraphQLSchema, build_schema, parse

from graphql_codegen import Config, generate
from tests._bookshop import BOOKSHOP_DIRECTORY


@pytest.mark.parametrize(
    ("path", "snippet"),
    [
        pytest.param(
            "schema/enum.py",
            "type Genre = _typing.Literal['FICTION', 'NONFICTION', 'POETRY', 'CHILDREN']",
            id="an enum is a closed literal",
        ),
        pytest.param(
            "schema/input.py",
            "lines: _typing.Required[_abc.Sequence[OrderLineInput]]",
            id="a non-null input field without default is required",
        ),
        pytest.param(
            "schema/input.py",
            "giftWrap: _typing.NotRequired[_builtins.bool]",
            id="a non-null input field with a default is not",
        ),
        pytest.param(
            "schema/input.py",
            "coupons: _typing.NotRequired[_abc.Sequence[_builtins.str]]",
            id="nor is a list with a default",
        ),
        pytest.param(
            "schema/input.py",
            "note: _typing.NotRequired[_builtins.str | None]",
            id="a nullable input field may be left out or null",
        ),
        pytest.param(
            "schema/input.py",
            "type BookLookup = _BookLookup_id | _BookLookup_isbn",
            id="a oneOf input is a union of single-key dicts",
        ),
        pytest.param(
            "schema/input.py",
            "class _BookLookup_id(_compat.TypedDict, closed=True):\n    id: _typing.Required[_builtins.str]",
            id="the key a oneOf member sets is not null",
        ),
        pytest.param(
            "schema/input.py",
            "from __future__ import annotations",
            id="input types reference each other lazily",
        ),
        pytest.param(
            "schema/input.py",
            "_BookFilter_and = _compat.TypedDict('_BookFilter_and', {'and': _typing.Required[_abc.Sequence[_BookFilter_Ref]]}, closed=True)",
            id="a keyword key is declared functionally, and a recursive reference deferred",
        ),
        pytest.param(
            "../app_graphql.py",
            "type _GetPublicationData_publication = _GetPublicationData_publication_Book | _GetPublicationData_publication_Magazine | _GetPublicationData_publication_Audiobook",
            id="an interface chain gives a member per concrete type",
        ),
        pytest.param(
            "../app_graphql.py",
            "class _GetPublicationData_publication_Book(_GetPublicationData_publication_Book_Base, closed=True):\n    title: _builtins.str\n    pages: _builtins.int\n    isbn: _scalar.ISBN\n\n",
            id="a member gathers the fields of every condition it meets",
        ),
        pytest.param(
            "../app_graphql.py",
            "class _GetPublicationData_publication_Magazine(_GetPublicationData_publication_Magazine_Base, closed=True):\n    title: _builtins.str\n    pages: _builtins.int\n\n",
            id="and only those",
        ),
        pytest.param(
            "../app_graphql.py",
            "class _GetPublicationData_publication_Audiobook(_GetPublicationData_publication_Audiobook_Base, closed=True):\n    title: _builtins.str\n    duration: _builtins.int\n",
            id="each with the fields of its own condition",
        ),
        pytest.param(
            "../app_graphql.py",
            "{'__typename': _compat.ReadOnly[_typing.Literal['Audiobook']]}",
            id="a member is tagged with its typename",
        ),
        pytest.param(
            "../app_graphql.py",
            "class _GetBookData_book(BookCard, closed=True):",
            id="a fragment spread is a base class, by name in its own document's module",
        ),
        pytest.param(
            "../app_graphql.py",
            'book: _typing.Annotated[_GetBookData_book, _reflection.NON_NULL]\n    """The book with this identifier or ISBN."""',
            id="a field's description is its docstring",
        ),
        pytest.param(
            "../app_graphql.py",
            "reviews: _typing.NotRequired[_builtins.list[_GetBookData_book_reviews]]",
            id="a field include may leave out is not required",
        ),
        pytest.param(
            "../app_graphql.py",
            'lookup: _typing.Required[_input.BookLookup]\n    """An identifier or an ISBN."""',
            id="a variable's description is its docstring",
        ),
        pytest.param(
            "../app_graphql.py",
            'data_type=GetBookData)\n"""A book, with its reviews when asked for."""',
            id="an operation's description is its constant's docstring",
        ),
        pytest.param(
            "../app_graphql.py",
            'class BookCard(_compat.TypedDict):\n    """What a book shows in a list."""',
            id="a fragment's description is its type's docstring",
        ),
        pytest.param(
            "../app_graphql.py",
            "_runtime.Operation(operation_type='subscription', name='OnOrderStatusChanged',",
            id="a subscription is an operation of its type",
        ),
    ],
)
def test_the_bookshop_client_holds(
    path: str, snippet: str, bookshop_files: Mapping[PurePosixPath, bytes]
) -> None:
    assert snippet in bookshop_files[PurePosixPath(path)].decode()


@pytest.mark.parametrize(
    ("path", "snippet"),
    [
        pytest.param(
            "schema/input.py", "RestockInput", id="an input type no operation reaches"
        ),
        pytest.param("schema/enum.py", "Membership", id="an enum no operation reaches"),
        pytest.param(
            "schema/input.py",
            "TYPE_CHECKING",
            id="an import guard, needless since inputs reference each other lazily",
        ),
        pytest.param(
            "schema/enum.py",
            "%future added value",
            id="an enum sentinel, needless since nothing validates",
        ),
        pytest.param(
            "../app_graphql.py",
            "subscription OnOrderStatusChanged($orderId§",
            id="a merge sigil in a subscription, which cannot be merged",
        ),
    ],
)
def test_the_bookshop_client_lacks(
    path: str, snippet: str, bookshop_files: Mapping[PurePosixPath, bytes]
) -> None:
    assert snippet not in bookshop_files[PurePosixPath(path)].decode()


def test_only_a_package_re_exports(
    bookshop_files: Mapping[PurePosixPath, bytes],
) -> None:
    """A module of a package, or next to a document, exports only what it defines: none imports a name as itself, PEP 484's re-export, however its helpers' aliases are settled."""
    for path, content in bookshop_files.items():
        if path.name != "__init__.py" and path.parts[0] != "runtime":
            assert not [
                node.name
                for node in ast.walk(ast.parse(content))
                if isinstance(node, ast.alias) and node.asname == node.name
            ], path


def test_typename_is_never_declared_in_a_class_body(
    bookshop_files: Mapping[PurePosixPath, bytes],
) -> None:
    """Python mangles `__typename` in a class body, silently breaking every payload."""
    for path, source in bookshop_files.items():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assert node.target.id != "__typename", path


def test_a_document_goes_on_the_wire_triple_quoted_without_what_only_the_client_needs(
    bookshop_files: Mapping[PurePosixPath, bytes],
) -> None:
    """Descriptions and `@nonNull` leave it, and it reads as GraphQL wherever it sits."""
    source = bookshop_files[PurePosixPath("../app_graphql.py")].decode()
    document = source.split('document="""')[1].split('"""')[0]

    assert document.startswith(
        "query GetBook($lookup§: BookLookup!, $withReviews§: Boolean! = false) {\n"
    )
    assert "\\n" not in document
    assert '"' not in document
    assert "@nonNull" not in document


@pytest.mark.parametrize(
    ("document", "present", "absent"),
    [
        pytest.param(
            'query Q { publication(id: "1") { __typename ... on Book { ...PageCount } } } fragment PageCount on Printed { pageCount }',
            [
                "class _QData_publication_Book(_QData_publication_Book_Base, PageCount, closed=True):",
                "class _QData_publication_Magazine(_QData_publication_Magazine_Base, closed=True):\n    ...",
            ],
            [],
            id="a spread on an interface inside a branch is a base of that branch",
        ),
        pytest.param(
            'query Q { search(text: "Austen") { ...Result } } fragment Result on SearchResult { __typename ... on Author { name } }',
            [
                "class _QData_search_Author(_QData_search_Author_Base, closed=True):\n    name: _builtins.str"
            ],
            ["(Result)", "(Result,", ", Result)", ", Result,"],
            id="a fragment on a union is expanded into its members",
        ),
        pytest.param(
            'query Q { book(lookup: {id: "1"}) { ...Title ...Title } } fragment Title on Book { title }',
            ["    book: Title | None\n"],
            ["class _QData_book("],
            id="a field selecting one fragment and nothing else is typed by it, however often it spreads it",
        ),
        pytest.param(
            'query Q { book(lookup: {id: "1"}) { ...Title id } } fragment Title on Book { title }',
            ["class _QData_book(Title, closed=True):\n    id: _builtins.str"],
            [],
            id="a field selecting more than one fragment gets a class inheriting it",
        ),
        pytest.param(
            'query Q { book(lookup: {id: "1"}) { ...Title ...Pages } } fragment Title on Book { title } fragment Pages on Book { pageCount }',
            ["class _QData_book(Title, Pages, closed=True):"],
            [],
            id="two fragments are two bases",
        ),
        pytest.param(
            'query Q { book(lookup: {id: "1"}) { ...Card ...Title } } fragment Card on Book { ...Title isbn } fragment Title on Book { title }',
            ["class _QData_book(Card, Title, closed=True):"],
            [],
            id="a fragment and its own base are both bases",
        ),
        pytest.param(
            'query Q { book(lookup: {id: "1"}) { ...Title title } } fragment Title on Book { title }',
            ["class _QData_book(Title, closed=True):"],
            [],
            id="a key the selection declares alike is no conflict",
        ),
        pytest.param(
            'query Q { book(lookup: {id: "1"}) { ...AuthorName author { id } } } fragment AuthorName on Book { author { name } }',
            [
                "class _QData_book_author(_compat.TypedDict, closed=True):\n    id: _builtins.str\n    name: _builtins.str"
            ],
            ["(AuthorName)", "(AuthorName,", ", AuthorName)", ", AuthorName,"],
            id="a fragment declaring a key otherwise than the selection is expanded",
        ),
        pytest.param(
            'query Q { book(lookup: {id: "1"}) { ...AuthorName ...AuthorId } } fragment AuthorName on Book { author { name } } fragment AuthorId on Book { author { id } }',
            [
                "class _QData_book_author(_compat.TypedDict, closed=True):\n    name: _builtins.str\n    id: _builtins.str"
            ],
            [
                "(AuthorName)",
                "(AuthorName,",
                ", AuthorName)",
                ", AuthorName,",
                "(AuthorId)",
                "(AuthorId,",
                ", AuthorId)",
                ", AuthorId,",
            ],
            id="two fragments declaring a key differently are expanded",
        ),
        pytest.param(
            'query Q { book(lookup: {id: "1"}) { ...Card ...Title author { id } } } fragment Card on Book { ...Title author { name } } fragment Title on Book { title }',
            [
                "class _QData_book(Title, closed=True):",
                "class _QData_book_author(_compat.TypedDict, closed=True):\n    id: _builtins.str\n    name: _builtins.str",
            ],
            ["(Card)", "(Card,", ", Card)", ", Card,"],
            id="expanding a fragment keeps the base it spreads",
        ),
        pytest.param(
            'query Q($withGenre: Boolean!) { book(lookup: {id: "1"}) { title genre @include(if: $withGenre) never: genre @include(if: false) always: genre @skip(if: false) } }',
            [
                "    title: _builtins.str\n    genre: _typing.NotRequired[_enum.Genre]\n    always: _enum.Genre\n"
            ],
            ["never: _"],
            id="a field skip or include may leave out is not required",
        ),
        pytest.param(
            'query Q($withTitle: Boolean!) { book(lookup: {id: "1"}) { genre ...Title @include(if: $withTitle) } } fragment Title on Book { title }',
            ["    genre: _enum.Genre\n    title: _typing.NotRequired[_builtins.str]\n"],
            ["(Title)", "(Title,", ", Title)", ", Title,"],
            id="a conditional spread is expanded, its keys not required",
        ),
        pytest.param(
            'query Q($withGenre: Boolean!) { book(lookup: {id: "1"}) { title ... @include(if: $withGenre) { title genre } } }',
            ["    title: _builtins.str\n    genre: _typing.NotRequired[_enum.Genre]\n"],
            [],
            id="a field selected unconditionally somewhere is required",
        ),
        pytest.param(
            'query Q { __type(name: "Book") { kind } }',
            ["kind: _typing.Literal['SCALAR', 'OBJECT',"],
            ["_enum."],
            id="an introspection enum is spelled where it is selected",
        ),
        pytest.param(
            'query Q { search(text: """Austen""") { __typename ... on Author { name } } }',
            ['search(text: "Austen")'],
            [],
            id="a block string goes on the wire as an ordinary one",
        ),
        pytest.param(
            'query Q { publication(id: "1") { title } }',
            [
                "'__typename': _compat.ReadOnly[_typing.Literal['Book', 'Magazine', 'Audiobook']]",
                'publication(id: "1") {\n    __typename\n    title',
            ],
            [],
            id="a selection on an abstract type gets `__typename`, even without branches",
        ),
        pytest.param(
            'query Q { search(text: "Austen") { ... on Publication { title ... on Book { isbn } } } }',
            [
                'search(text: "Austen") {\n    __typename\n    ... on Publication {\n      title'
            ],
            ["... on Publication {\n      __typename"],
            id="an inline fragment's selections get none, gathered into the branches the enclosing `__typename` tells apart",
        ),
        pytest.param(
            'query Q { search(text: "Austen") { __typename ... on Author { name } } }',
            [],
            ["__typename\n    __typename"],
            id="a selection already holding `__typename` gets no other",
        ),
        pytest.param(
            'query Q($x: Boolean!) { search(text: "Austen") { __typename @include(if: $x) ... on Author { name } } }',
            ["__typename\n    __typename @include(if: $x§)"],
            [],
            id="a conditional `__typename` gets an unconditional one, since the key must be present",
        ),
        pytest.param(
            'query Q { publication(id: "1") { __typename ... on Book { ...Pages } } } fragment Pages on Printed { __typename pageCount }',
            [
                "class _QData_publication_Book(_QData_publication_Book_Base, Pages, closed=True):"
            ],
            [],
            id="an inline fragment's own selections need none, gathered into the branches the enclosing `__typename` tells apart",
        ),
        pytest.param(
            'query Q { publication(id: "1") { __typename ... on Printed { pageCount ... on Book { isbn } } } }',
            ["class _QData_publication_Book("],
            [],
            id="a fragment selecting `__typename` is still a base, whose key a branch narrows",
        ),
        pytest.param(
            'query Q { order(id: "1") { id } }',
            [
                "class _QData_order(_compat.TypedDict, closed=True):\n    id: _builtins.str"
            ],
            ["_enum", "_input"],
            id="an operation reaching no enum and no input imports neither",
        ),
    ],
)
def test_a_document_generates(
    document: str, present: list[str], absent: list[str], bookshop_schema: GraphQLSchema
) -> None:
    source = generate(
        document=parse(document), schema=bookshop_schema, config=Config()
    )[PurePosixPath("document/GraphQL_request.py")].decode()

    for snippet in present:
        assert snippet in source

    for snippet in absent:
        assert snippet not in source


@pytest.mark.parametrize(
    "description",
    [
        pytest.param('Ends with a "quote"', id="ending with a quote"),
        pytest.param('Holds """ in the middle', id="holding triple quotes"),
    ],
)
def test_a_description_is_the_docstring_of_what_it_describes_whatever_its_quotes(
    description: str,
) -> None:
    schema = build_schema(f"type Query {{ {json.dumps(description)} answer: Int }}")
    module = ast.parse(
        generate(document=parse("query Q { answer }"), schema=schema, config=Config())[
            PurePosixPath("document/GraphQL_request.py")
        ]
    )

    assert description in [
        node.value.value
        for node in ast.walk(module)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    ]


def test_the_package_needs_nothing_but_the_standard_library(tmp_path: Path) -> None:
    if sys.version_info < (3, 15):
        spec = find_spec("typing_extensions")
        assert spec is not None
        assert spec.origin is not None
        shutil.copy(spec.origin, tmp_path)

    subprocess.run(
        [
            sys.executable,
            # Ignores `PYTHON*` variables, the user's site-packages, and the script's directory.
            "-I",
            # No `site`, and so no site-packages.
            "-S",
            str(Path(__file__).parent / "__resources__" / "import_every_module.py"),
            "bookshop.client",
            str(BOOKSHOP_DIRECTORY.parent),
            str(tmp_path),
        ],
        check=True,
    )

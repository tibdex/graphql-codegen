import re
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Final

import pytest
from graphql import (
    DocumentNode,
    GraphQLError,
    GraphQLSchema,
    Source,
    build_schema,
    parse,
)

from graphql_codegen import (
    Codec,
    Config,
    DocumentSiblingModule,
    PackageLocation,
    Scalar,
    generate,
)
from tests._bookshop import BOOKSHOP_DIRECTORY

_BOOKSHOP_SCHEMA: Final = (BOOKSHOP_DIRECTORY / "schema.graphqls").read_text(
    encoding="utf-8"
)
_PRICE: Final = 'query Q { book(lookup: {id: "1"}) { price } }'
_SAVED_SEARCH: Final = 'query Q { savedSearch(name: "classics") { value } }'
_PLACE_ORDER: Final = (
    "mutation M($input: PlaceOrderInput!) { placeOrder(input: $input) { id } }"
)
_MONEY: Final = {
    "Money": Scalar(
        type="decimal.Decimal",
        codec=Codec(decode="my_app.money.decode", encode="my_app.money.encode"),
    )
}


@pytest.mark.parametrize(
    ("document", "config", "path", "present", "absent"),
    [
        pytest.param(
            _PRICE,
            Config(scalars=_MONEY),
            "schema.py",
            [
                "from decimal import Decimal as _Decimal",
                "from my_app.money import decode as _decode",
                "type Money = _typing.Annotated[_Decimal, _reflection.Codec(decode=_decode, encode=_encode)]",
            ],
            [],
            id="a codec scalar imports its paths absolutely, and never while generating: they need not exist yet",
        ),
        pytest.param(
            _PRICE,
            Config(scalars={"ISBN": Scalar(type="str")}),
            "schema.py",
            ["type ISBN = _builtins.str"],
            [],
            id="an identity scalar named by a bare name is a builtin",
        ),
        pytest.param(
            _PRICE,
            Config(
                scalars={
                    "Money": Scalar(
                        type="..money.Money",
                        codec=Codec(decode="..decode_money", encode="...money.encode"),
                    )
                }
            ),
            "schema.py",
            [
                "from ..money import Money as _Money",
                "from .. import decode_money as _decode_money",
                "from ...money import encode as _encode",
            ],
            [],
            id="a path starting with two dots or more is relative to the generated package's directory",
        ),
        pytest.param(
            _PRICE,
            Config(),
            "document/GraphQL_request.py",
            ["price: _builtins.object"],
            [],
            id="a custom scalar left out of the config is typed `object`",
        ),
        pytest.param(
            _SAVED_SEARCH,
            Config(struct_interface_name="Struct"),
            "document/GraphQL_request.py",
            ["value: _schema.BookFilter | None"],
            [],
            id="a struct payload is typed with its input type",
        ),
        pytest.param(
            _SAVED_SEARCH,
            Config(struct_interface_name="Struct"),
            "schema.py",
            ["type BookFilter = "],
            ["PlaceOrderInput"],
            id="which it reaches even when no variable does",
        ),
        pytest.param(
            _SAVED_SEARCH,
            Config(),
            "document/GraphQL_request.py",
            ["value: _builtins.object | None"],
            [],
            id="but only when the struct interface is named",
        ),
        pytest.param(
            'query Q { savedSearch(name: "classics") { value @nonNull } }',
            Config(non_null_directive_name="nonNull", struct_interface_name="Struct"),
            "document/GraphQL_request.py",
            ["value: _typing.Annotated[_schema.BookFilter, _reflection.NON_NULL]"],
            [],
            id="and not optional when asserted non-null",
        ),
        pytest.param(
            'query Q { book(lookup: {id: "1"}) @nonNull { title } }',
            Config(non_null_directive_name="nonNull"),
            "document/GraphQL_request.py",
            ["book: _typing.Annotated[_QData_book, _reflection.NON_NULL]"],
            ["@nonNull"],
            id="a field asserted non-null is not optional, and its directive stays off the wire",
        ),
        pytest.param(
            _PLACE_ORDER,
            Config(injector_names={"idempotencyKey"}),
            "schema.py",
            ["class PlaceOrderInput("],
            ["idempotencyKey"],
            id="an injected field is left out of its input type",
        ),
        pytest.param(
            _PLACE_ORDER,
            Config(injector_names={"city"}),
            "document/GraphQL_request.py",
            [
                "injections={'city': _builtins.frozenset({('input', 'shippingAddress')})}"
            ],
            [],
            id="and received wherever an input holds it, at any depth",
        ),
        pytest.param(
            _PLACE_ORDER,
            Config(injector_names={"note"}),
            "injection.py",
            [
                "note: _abc.Callable[[], _builtins.str | None | _OMITTED]",
                "def injectors(functions: InjectorFunctions, /) -> _injection._Injectors:",
            ],
            [],
            id="an injector is required, typed by what it injects, and may return `OMITTED` where it is nullable",
        ),
        pytest.param(
            f"{_PLACE_ORDER} query N($note: String!) {{ savedSearch(name: $note) {{ value }} }}",
            Config(injector_names={"note"}),
            "injection.py",
            ["note: _abc.Callable[[], _builtins.str]"],
            [],
            id="an injector takes the strictest nullability of its positions, since it cannot tell which one it is called for",
        ),
        pytest.param(
            "query Q($in: ID!) { order(id: $in) { id } }",
            Config(injector_names={"in"}),
            "injection.py",
            [
                "InjectorFunctions = _compat.TypedDict('InjectorFunctions', {'in': _abc.Callable[[], _builtins.str]}, closed=True)"
            ],
            [],
            id="an injector named like a keyword is declared functionally",
        ),
    ],
)
def test_a_config_field_shapes_the_package(
    document: str,
    config: Config,
    path: str,
    present: list[str],
    absent: list[str],
    bookshop_schema: GraphQLSchema,
) -> None:
    source = generate(document=parse(document), schema=bookshop_schema, config=config)[
        PurePosixPath(path)
    ].decode()

    for snippet in present:
        assert snippet in source

    for snippet in absent:
        assert snippet not in source


def _message(error: Exception, /) -> str:
    return error.message if isinstance(error, GraphQLError) else str(error)


@pytest.mark.parametrize(
    ("schema", "document", "config", "errors"),
    [
        pytest.param(
            _BOOKSHOP_SCHEMA,
            _PRICE,
            Config(
                document_sibling_module=DocumentSiblingModule(
                    name="_{document}_gql",
                    package_location=PackageLocation(
                        module_root=PurePosixPath("/src"), package="api"
                    ),
                ),
            ),
            [
                ValueError(
                    "Expected every operation and fragment to come from a source named by its path, absolute or relative to the same directory as the module root, so that its module can go next to its document."
                )
            ],
            id="an operation parsed from a string, whose source names no path",
        ),
        pytest.param(
            _BOOKSHOP_SCHEMA,
            _PRICE,
            Config(scalars={"Mony": Scalar(type="decimal.Decimal")}),
            [ValueError("Expected `Mony` to be a scalar of the schema.")],
            id="a scalar the schema lacks, a misspelled one for instance, which would configure nothing",
        ),
        pytest.param(
            "interface Node { id: ID } type Book implements Node { title: String } type Query { book: Book }",
            "query Q { book { title } }",
            Config(),
            [
                GraphQLError(
                    "Interface field Node.id expected but Book does not provide it."
                )
            ],
            id="a schema graphql-core does not validate",
        ),
        pytest.param(
            _BOOKSHOP_SCHEMA,
            _PRICE,
            Config(struct_interface_name="Missing"),
            [ValueError("Found no interface named `Missing`.")],
            id="a struct interface the schema lacks",
        ),
        pytest.param(
            "scalar Opaque interface Struct { value: Opaque, other: Int } type Query { a: Int }",
            "query A { a }",
            Config(struct_interface_name="Struct"),
            [
                GraphQLError(
                    "Expected `Struct` to have a single field, the payload, of a custom scalar type."
                )
            ],
            id="a struct interface with more than its payload",
        ),
        pytest.param(
            "scalar Opaque interface Struct { value: Opaque } type Wrong implements Struct { value: Opaque } type Query { a: Wrong }",
            "query A { a { value } }",
            Config(struct_interface_name="Struct"),
            [
                GraphQLError(
                    "Expected `Wrong`, which implements `Struct`, to be named after an input type followed by `Struct`."
                )
            ],
            id="a struct named after no input type",
        ),
        pytest.param(
            "directive @nonNull on FIELD type Query { a: Int }",
            "query A { a }",
            Config(non_null_directive_name="nonNull"),
            [
                ValueError(
                    "Expected the schema to declare no `@nonNull`, the name of the client directive."
                )
            ],
            id="a schema declaring the non-null directive, which would make it a server directive, sent rather than stripped",
        ),
        pytest.param(
            _BOOKSHOP_SCHEMA,
            _PLACE_ORDER,
            Config(injector_names={"quantity"}),
            [
                GraphQLError(
                    "Cannot inject into `$input`: an injected field is reached through a list or a recursive input type, which no static path can express."
                )
            ],
            id="an injected field reached through a list, which no static path expresses",
        ),
        pytest.param(
            _BOOKSHOP_SCHEMA,
            "query Q($filter: BookFilter) { books(filter: $filter) { id } }",
            Config(injector_names={"genre"}),
            [
                GraphQLError(
                    "Cannot inject into `$filter`: an injected field is reached through a list or a recursive input type, which no static path can express."
                )
            ],
            id="or through a recursive input type, reported once however many paths reach it",
        ),
        pytest.param(
            _BOOKSHOP_SCHEMA,
            f"query Q($note: Int) {{ books(first: $note) {{ id }} }} {_PLACE_ORDER}",
            Config(injector_names={"note"}),
            [
                GraphQLError(
                    "Expected `note` to have one type, nullability aside, wherever it is injected, but it is `Int` at `$note` in `Q` and `String` at `$input.note` in `M`."
                )
            ],
            id="an injector with two types, since it supplies one value",
        ),
        pytest.param(
            _BOOKSHOP_SCHEMA,
            _PRICE,
            Config(injector_names={"idempotencyKey"}),
            [
                ValueError(
                    "Expected `idempotencyKey` to be injected somewhere, but no operation sends a variable or an input field of that name."
                )
            ],
            id="an injector injected nowhere, a misspelled one for instance, which would have no type",
        ),
        pytest.param(
            _BOOKSHOP_SCHEMA,
            "{ books { id } }",
            Config(),
            [
                GraphQLError(
                    "Expected the operation to be named to name its constant after it."
                )
            ],
            id="an anonymous operation, which GraphQL allows alone in its document",
        ),
        pytest.param(
            _BOOKSHOP_SCHEMA,
            'query Q { search(text: "Austen") { ...Missing ...OnMissing ... on Author { name @include } } } fragment OnMissing on Missing { name }',
            Config(),
            [
                GraphQLError("Unknown fragment 'Missing'."),
                GraphQLError(
                    "Argument '@include(if:)' of type 'Boolean!' is required, but it was not provided."
                ),
                GraphQLError("Unknown type 'Missing'."),
            ],
            id="an unknown fragment or type, or `@include` without `if`, reported by the spec's rules even though `__typename` is inserted before they run",
        ),
        pytest.param(
            _BOOKSHOP_SCHEMA,
            'query Q { book(lookup: {id: "1"}) @nonNull { title } }',
            Config(),
            [GraphQLError("Unknown directive '@nonNull'.")],
            id="the non-null directive unless named",
        ),
    ],
)
def test_an_input_is_rejected(
    schema: str, document: str, config: Config, errors: list[Exception]
) -> None:
    with pytest.raises((ExceptionGroup, GraphQLError, ValueError)) as error_info:
        generate(document=parse(document), schema=build_schema(schema), config=config)

    error = error_info.value
    assert [
        (type(raised), _message(raised))
        for raised in (
            error.exceptions if isinstance(error, ExceptionGroup) else [error]
        )
    ] == [(type(expected), _message(expected)) for expected in errors]


@pytest.mark.parametrize(
    ("construct", "error"),
    [
        pytest.param(
            lambda: Config(non_null_directive_name="non-null"),
            "Cannot name the client directive `non-null`: Names must only contain [_a-zA-Z0-9] but 'non-null' does not.",
            id="a non-null directive name GraphQL does not allow, checked by graphql-core",
        ),
        pytest.param(
            lambda: Scalar(type="Decimal"),
            "Expected a builtin (`str`), an absolute dotted name (`decimal.Decimal`), or a path relative to the generated package's directory (`..scalar.decode_datetime`), but got `Decimal`.\nIn `type`.",
            id="a scalar type named by an unknown builtin, since the config may come from a file, which static typing does not check",
        ),
        pytest.param(
            lambda: Scalar(type="decimal.2"),
            "Expected a builtin (`str`), an absolute dotted name (`decimal.Decimal`), or a path relative to the generated package's directory (`..scalar.decode_datetime`), but got `decimal.2`.\nIn `type`.",
            id="a scalar type that is not a dotted name",
        ),
    ],
)
def test_a_config_field_cannot_be_constructed_invalid(
    construct: Callable[[], object], error: str
) -> None:
    with pytest.raises(ValueError, match=f"^{re.escape(error)}$"):
        construct()


def _documents(sources: dict[str, str], /) -> DocumentNode:
    return DocumentNode(
        definitions=tuple(
            definition
            for name, body in sources.items()
            for definition in parse(Source(body, name)).definitions
        )
    )


def _generated(
    sources: dict[str, str],
    /,
    *,
    sibling_module_name: str | None,
    bookshop_schema: GraphQLSchema,
) -> dict[PurePosixPath, bytes]:
    return generate(
        document=_documents({f"app/{name}": body for name, body in sources.items()}),
        schema=bookshop_schema,
        config=Config(
            document_sibling_module=None
            if sibling_module_name is None
            else DocumentSiblingModule(
                name=sibling_module_name,
                package_location=PackageLocation(
                    module_root=PurePosixPath(), package="api"
                ),
            ),
        ),
    )


def test_a_document_s_module_is_named_after_it_as_a_module_can_be(
    bookshop_schema: GraphQLSchema,
) -> None:
    files = _generated(
        {
            "_restrictions.graphql": "query A { books { title } }",
            "2-fast.graphql": "query B { books { ...Card } }",
            "card.graphql": "fragment Card on Book { title }",
        },
        sibling_module_name="_{document}_gql",
        bookshop_schema=bookshop_schema,
    )

    assert sorted(path for path in files if path.parts[0] == "..") == [
        PurePosixPath("../app/_2_fast_gql.py"),
        PurePosixPath("../app/_card_gql.py"),
        PurePosixPath("../app/_restrictions_gql.py"),
    ]
    assert PurePosixPath("document/__init__.py") not in files


def test_a_fragment_of_another_document_is_imported_from_its_module(
    bookshop_schema: GraphQLSchema,
) -> None:
    files = _generated(
        {
            "card.graphql": "fragment Card on Book { title }",
            "list.graphql": "query List { books { ...Card } }",
        },
        sibling_module_name=None,
        bookshop_schema=bookshop_schema,
    )
    source = files[PurePosixPath("document/list.py")].decode()

    assert "from . import card as _card" in source
    assert "books: _builtins.list[_card.Card]" in source


def test_a_fragment_goes_next_to_the_operations_of_its_document_and_is_imported_absolutely_elsewhere(
    bookshop_schema: GraphQLSchema,
) -> None:
    files = _generated(
        {
            "card.graphql": "query Get { books { ...GetData } } fragment GetData on Book { title }",
            "list.graphql": "query List { books { ...GetData } }",
        },
        sibling_module_name="{document}_graphql",
        bookshop_schema=bookshop_schema,
    )
    card = files[PurePosixPath("../app/card_graphql.py")].decode()

    assert (
        "class GetData_1(_compat.TypedDict, closed=True):\n    books: _builtins.list[GetData]"
        in card
    )
    assert (
        "from app import card_graphql as _card_graphql"
        in files[PurePosixPath("../app/list_graphql.py")].decode()
    )


@pytest.mark.parametrize(
    ("sources", "sibling_module_name", "message"),
    [
        pytest.param(
            {
                "Cube.graphql": "query A { books { title } }",
                "cube.graphql": "query B { books { title } }",
            },
            "{document}",
            "Expected `app/Cube.graphql` and `app/cube.graphql` to have modules of their own, but both would be `app/cube.py`, case aside.",
            id="or differing only by case, which some file systems ignore",
        ),
        pytest.param(
            {"__init__.graphql": "query A { books { title } }"},
            "{document}",
            "Expected `app/__init__.graphql` to name a module, but `__init__` is its package's own.",
            id="a document named like a package's own module",
        ),
        pytest.param(
            {
                "list.graphql": "query List { books { ...Card } }",
                "sub-dir/card.graphql": "fragment Card on Book { title }",
            },
            "{document}",
            "Expected `app/sub-dir/card.graphql`'s module, `app/sub-dir/card.py`, to be importable from the module root, since another document spreads its fragments.",
            id="a module another document imports fragments from, which no dotted name reaches",
        ),
    ],
)
def test_a_document_s_module_depends_on_its_document_alone(
    sources: dict[str, str],
    sibling_module_name: str | None,
    message: str,
    bookshop_schema: GraphQLSchema,
) -> None:
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        _generated(
            sources,
            sibling_module_name=sibling_module_name,
            bookshop_schema=bookshop_schema,
        )


def test_a_document_s_module_holds_its_operations_importing_the_package_absolutely(
    bookshop_schema: GraphQLSchema,
) -> None:
    source = _generated(
        {
            "orders.graphql": 'query Get { order(id: "1") { status } } query GetData { books { title } }'
        },
        sibling_module_name="{document}",
        bookshop_schema=bookshop_schema,
    )[PurePosixPath("../app/orders.py")].decode()

    assert source.startswith(
        "# Code generated by graphql-codegen from `orders.graphql`. DO NOT EDIT.\n\nimport "
    )

    for snippet in [
        "from api import runtime as _runtime",
        "from api import schema as _schema\n",
        "class GetData_1(_compat.TypedDict, closed=True):",
        "Get: _typing.Final = _runtime.Operation(operation_type=_operation_type.QUERY, name='Get', document=",
        "GetData: _typing.Final = _runtime.Operation(operation_type=_operation_type.QUERY, name='GetData', document=",
    ]:
        assert snippet in source


def test_a_fragment_s_type_follows_those_of_the_fragments_it_spreads(
    bookshop_schema: GraphQLSchema,
) -> None:
    source = generate(
        document=parse(
            "query Q { books { ...Listing } } fragment Listing on Book { ...Card price } fragment Card on Book { id title }"
        ),
        schema=bookshop_schema,
        config=Config(),
    )[PurePosixPath("document/GraphQL_request.py")].decode()

    assert source.index("class Card(") < source.index("class Listing(Card")

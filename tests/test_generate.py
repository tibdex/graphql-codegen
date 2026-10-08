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
            "_scalar.py",
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
            "_scalar.py",
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
            "_scalar.py",
            [
                "from ..money import Money as _Money",
                "from .. import decode_money as _decode_money",
                "from ...money import encode as _encode",
            ],
            [],
            id="a path starting with two dots or more is relative to the package's directory",
        ),
        pytest.param(
            _PRICE,
            Config(),
            "document/GraphQL_request.py",
            ["price: _builtins.object"],
            [],
            id="an unconfigured custom scalar is opaque",
        ),
        pytest.param(
            _SAVED_SEARCH,
            Config(struct_interface_name="Struct"),
            "document/GraphQL_request.py",
            ["value: _input.BookFilter | None"],
            [],
            id="a struct payload is typed with its input type",
        ),
        pytest.param(
            _SAVED_SEARCH,
            Config(struct_interface_name="Struct"),
            "schema/input.py",
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
            ["value: _typing.Annotated[_input.BookFilter, _reflection.NON_NULL]"],
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
            "schema/input.py",
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
            Config(injector_names={"idempotencyKey"}),
            "injection.py",
            [
                "idempotencyKey: _typing.NotRequired[_abc.Callable[[], _builtins.object | None | _OMITTED]]",
                "def injectors(functions: InjectorFunctions, /) -> _injection._Injectors:",
            ],
            [],
            id="an injector is typed by what it injects, and may be left out or return `OMITTED` where it is nullable",
        ),
        pytest.param(
            f'{_PLACE_ORDER} mutation N($idempotencyKey: UUID!) {{ cancelOrder(input: {{order: "o1", idempotencyKey: $idempotencyKey}}) {{ id }} }}',
            Config(injector_names={"idempotencyKey"}),
            "injection.py",
            [
                "idempotencyKey: _typing.NotRequired[_abc.Callable[[], _builtins.object]]"
            ],
            [],
            id="an injector takes the strictest nullability of its positions, since it cannot tell which one it is called for",
        ),
        pytest.param(
            "query Q($in: ID!) { order(id: $in) { id } }",
            Config(injector_names={"in"}),
            "injection.py",
            [
                "InjectorFunctions = _compat.TypedDict('InjectorFunctions', {'in': _typing.NotRequired[_abc.Callable[[], _builtins.str]]}, closed=True)"
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
    """Its message alone, without the location a `GraphQLError` renders."""
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
                        module_root=PurePosixPath("/src"), package="client"
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
    """A wrong config value raises a `ValueError`, and a problem in the document or the schema a `GraphQLError`, graphql-core's validation errors all in one group."""
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
            "Expected an absolute dotted name, such as `datetime.datetime`, a dotted name relative to the package's directory, such as `..scalars.decode`, or a builtin, such as `int`, but got `Decimal`.\nIn `type`.",
            id="a scalar type named by an unknown builtin, since the config may come from a file, which static typing does not check",
        ),
        pytest.param(
            lambda: Scalar(type="decimal.2"),
            "Expected an absolute dotted name, such as `datetime.datetime`, a dotted name relative to the package's directory, such as `..scalars.decode`, or a builtin, such as `int`, but got `decimal.2`.\nIn `type`.",
            id="a scalar type that is not a dotted name",
        ),
    ],
)
def test_a_config_field_cannot_be_constructed_invalid(
    construct: Callable[[], object], error: str
) -> None:
    """Whether the config comes from Python or from a config file, with a note naming the field that is wrong where the message does not."""
    with pytest.raises(ValueError, match=f"^{re.escape(error)}$"):
        construct()


def test_a_problem_found_on_a_node_without_location_is_its_bare_message(
    bookshop_schema: GraphQLSchema,
) -> None:
    """As for a document parsed with `no_location=True`, which `generate()` accepts too."""
    with pytest.raises(GraphQLError) as error_info:
        generate(
            document=parse(_PLACE_ORDER, no_location=True),
            schema=bookshop_schema,
            config=Config(injector_names={"quantity"}),
        )

    assert str(error_info.value) == error_info.value.message


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
    """The package `client` for documents named by the keys of *sources*, under `app/`, their modules named *sibling_module_name* in `app/`, or in the package when ``None``."""
    return generate(
        document=_documents({f"app/{name}": body for name, body in sources.items()}),
        schema=bookshop_schema,
        config=Config(
            document_sibling_module=None
            if sibling_module_name is None
            else DocumentSiblingModule(
                name=sibling_module_name,
                package_location=PackageLocation(
                    module_root=PurePosixPath(), package="client"
                ),
            ),
        ),
    )


def test_a_document_s_module_is_named_after_it_as_a_module_can_be(
    bookshop_schema: GraphQLSchema,
) -> None:
    files = _generated(
        {
            # An underscore the name puts next to the stem merges with the stem's own.
            "_restrictions.graphql": "query A { books { title } }",
            # A character no identifier holds becomes `_`, and a leading digit gets one before it.
            "2-fast.graphql": "query B { books { ...Card } }",
            # A document of fragments alone has a module too.
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


def test_a_name_the_document_package_re_exports_steps_aside_for_modules_then_operations(
    bookshop_schema: GraphQLSchema,
) -> None:
    """Rather than being rejected, since a document is easy to rename the day it matters."""
    files = _generated(
        {
            "Get.graphql": "query Other { books { title } }",
            "other.graphql": "query Get { books { ...Other } } fragment Other on Book { title }",
        },
        sibling_module_name=None,
        bookshop_schema=bookshop_schema,
    )
    reexports = files[PurePosixPath("document/__init__.py")].decode()

    assert "from .Get import Other as Other" in reexports
    assert "from .other import Get_1 as Get_1, Other_1 as Other_1" in reexports


def test_a_fragment_goes_next_to_the_operations_of_its_document(
    bookshop_schema: GraphQLSchema,
) -> None:
    """Imported, absolutely, by the module of another document spreading it, and spelled clear of its own document's names: an operation's data type gives way."""
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
        "from client import runtime as _runtime",
        "from client.schema import enum as _enum\n",
        "class GetData_1(_compat.TypedDict, closed=True):",
        "Get: _runtime.Operation[_typing.Literal['query'], GetVariables, GetData_1]",
        "GetData: _runtime.Operation[_typing.Literal['query'], GetDataVariables, GetDataData]",
    ]:
        assert snippet in source


def test_the_document_package_re_exports_lazily_from_python_3_15(
    bookshop_schema: GraphQLSchema,
) -> None:
    source = generate(
        document=parse("query Q { books { title } }"),
        schema=bookshop_schema,
        config=Config(),
    )[PurePosixPath("document/__init__.py")].decode()

    assert (
        source
        == "# Code generated by graphql-codegen. DO NOT EDIT.\n\n__lazy_modules__ = [__name__ + '.GraphQL_request']\n\nfrom .GraphQL_request import Q as Q\n"
    )


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

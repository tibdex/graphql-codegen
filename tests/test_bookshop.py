import json
import runpy
from asyncio import run as run_async
from collections.abc import Callable, Mapping
from decimal import Decimal
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Final, cast
from uuid import UUID

import httpx2
import pytest

import bookshop.async_transport
import bookshop.injectors
import bookshop.transport
from bookshop.app import (
    book_and_similar,
    cancel,
    catalog,
    cheaper_than,
    describe,
    length,
    look_up,
    order,
    order_ids,
    pages,
    run_saved_search,
    status_label,
    titles,
    track,
)
from bookshop.async_app import title, watch as watch_async
from bookshop.get_order_graphql import GetOrder
from bookshop.graphql.injection import injectors
from bookshop.graphql.runtime import RequestError, UnexpectedNullError
from bookshop.graphql.schema import Address, Genre, OrderStatus
from bookshop.scalar import ISBN
from tests._bookshop import BOOKSHOP_DIRECTORY
from tests._server import PostHandler

_KEY: Final = UUID("6f1c2b9e-0d4a-4c3e-9b7a-2e5f8d1c3a40")
_BOOK: Final = {
    "id": "1",
    "title": "Persuasion",
    "price": "8.99",
    "author": {"name": "Jane Austen"},
    "isbn": "9780141439518",
    "pageCount": 249,
}
_ORDER: Final = {"id": "o1", "status": "SHIPPED"}


type _Handle = Callable[[httpx2.Request], httpx2.Response]
type _Respond = Callable[[Mapping[str, object]], object]
type _StartServer = Callable[[PostHandler], None]


def test_the_bookshop_type_checks(assert_type_checks: Callable[[Path], None]) -> None:
    assert_type_checks(BOOKSHOP_DIRECTORY)


@pytest.fixture(name="mocked_http")
def mocked_http_fixture(monkeypatch: pytest.MonkeyPatch) -> Callable[[_Handle], None]:
    def mock(handle: _Handle, /) -> None:
        monkeypatch.setattr(
            bookshop.async_transport,
            "http",
            httpx2.AsyncClient(
                transport=httpx2.MockTransport(handle),
                base_url=bookshop.async_transport.http.base_url,
                headers=bookshop.async_transport.http.headers,
            ),
        )

    return mock


@pytest.fixture(name="fixed_key")
def fixed_key_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bookshop.transport,
        "INJECTORS",
        injectors(
            {
                "idempotencyKey": lambda: _KEY,
                "customer": bookshop.injectors.customer,
            }
        ),
    )


def _answering(respond: _Respond, /) -> PostHandler:
    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        request = json.loads(handler.rfile.read(int(handler.headers["Content-Length"])))
        payload = json.dumps(respond(request)).encode()
        handler.send_response(200)
        handler.end_headers()
        handler.wfile.write(payload)

    return handle


def _events(*statuses: str) -> httpx2.Response:
    events = [
        f"event: next\ndata: {json.dumps({'data': {'orderStatusChanged': {'id': 'o1', 'status': status}}})}\n\n"
        for status in statuses
    ]
    return httpx2.Response(
        200,
        content="".join([*events, "event: complete\ndata:\n\n"]).encode(),
        headers={"Content-Type": "text/event-stream"},
    )


def test_a_title_is_awaited_with_a_timeout(
    mocked_http: Callable[[_Handle], None],
) -> None:
    def handle(request: httpx2.Request, /) -> httpx2.Response:
        assert request.url.path == "/graphql"
        assert request.extensions["timeout"]["read"] == 5.0
        return httpx2.Response(200, json={"data": {"book": _BOOK}})

    mocked_http(handle)
    book_title = run_async(title(ISBN("9780141439518")))

    assert book_title == "Persuasion"


def test_an_http_error_is_httpx2_s_own(mocked_http: Callable[[_Handle], None]) -> None:
    mocked_http(
        lambda _request: httpx2.Response(502, content=b"<html>Bad Gateway</html>")
    )

    with pytest.raises(httpx2.HTTPStatusError) as error_info:
        run_async(bookshop.async_transport.send(GetOrder({"id": "o1"})))

    assert (
        error_info.value.response.status_code,
        error_info.value.response.content,
    ) == (
        502,
        b"<html>Bad Gateway</html>",
    )


def test_a_request_error_is_read_whatever_its_status(
    mocked_http: Callable[[_Handle], None],
) -> None:
    mocked_http(
        lambda _request: httpx2.Response(
            400,
            content=b'{"errors": [{"message": "Syntax Error."}]}',
            headers={"Content-Type": "application/graphql-response+json"},
        )
    )

    with pytest.raises(RequestError, match="Syntax Error"):
        run_async(bookshop.async_transport.send(GetOrder({"id": "o1"})))


@pytest.mark.parametrize(
    ("events", "statuses"),
    [
        pytest.param(
            ["SHIPPED", "DELIVERED", "CANCELED"],
            ["SHIPPED", "DELIVERED"],
            id="until the order is delivered",
        ),
        pytest.param(["SHIPPED"], ["SHIPPED"], id="until the server completes"),
    ],
)
def test_watching_an_order_asynchronously_follows_its_statuses(
    events: list[str], statuses: list[str], mocked_http: Callable[[_Handle], None]
) -> None:
    mocked_http(lambda _request: _events(*events))

    assert run_async(watch_async("o1")) == statuses


def test_describing_a_book(bookshop_server: _StartServer) -> None:
    bookshop_server(_answering(lambda _request: {"data": {"book": _BOOK}}))

    assert describe(ISBN("9780141439518")) == "Persuasion, by Jane Austen, costs 8.99."


def test_describing_a_missing_book(bookshop_server: _StartServer) -> None:
    bookshop_server(_answering(lambda _request: {"data": {"book": None}}))

    with pytest.raises(UnexpectedNullError) as error_info:
        describe(ISBN("9780000000000"))

    assert error_info.value.path == ["book"]
    assert error_info.value.__notes__ == [
        "Raised by `GetBook` with variables {'lookup': {'isbn': '9780000000000'}}."
    ]


def test_a_book_and_similar_publications_come_in_one_request(
    bookshop_server: _StartServer,
) -> None:
    def respond(request: Mapping[str, object], /) -> object:
        assert request["operationName"] == "MergedOperation"
        search = [
            {"__typename": "Book", "title": "Emma", "price": "7.99"},
            {"__typename": "Author", "name": "Jane Austen"},
        ]
        return {"data": {"book_0": _BOOK, "search_1": search}}

    bookshop_server(_answering(respond))

    assert book_and_similar(ISBN("9780141439518"), "Austen") == (
        "Persuasion",
        ["Emma", "Jane Austen"],
    )


def test_books_and_publications_are_looked_up_in_one_request(
    bookshop_server: _StartServer,
) -> None:
    def respond(request: Mapping[str, object], /) -> object:
        assert request["operationName"] == "MergedOperation"
        publication = {"__typename": "Audiobook", "title": "Emma", "duration": 723}
        return {"data": {"book_0": _BOOK, "publication_1": publication}}

    bookshop_server(_answering(respond))
    results = look_up([ISBN("9780141439518")], ["p1"])

    assert [list(data) for data in results] == [["book"], ["publication"]]


def test_a_saved_search_runs_with_its_filter_as_saved(
    bookshop_server: _StartServer,
) -> None:
    saved = {"and": [{"genre": "FICTION"}, {"not": {"author": "a1"}}]}
    requests: list[Mapping[str, object]] = []

    def respond(request: Mapping[str, object], /) -> object:
        requests.append(request)
        return (
            {"data": {"savedSearch": {"value": saved}}}
            if request["operationName"] == "GetSavedSearch"
            else {"data": {"books": [{**_BOOK, "genre": "FICTION"}]}}
        )

    bookshop_server(_answering(respond))
    titles = run_saved_search("classics")

    assert titles == ["Persuasion"]
    assert requests[1]["variables"] == {"filter": saved}


@pytest.mark.parametrize(
    ("publication", "description"),
    [
        pytest.param(
            {
                "__typename": "Book",
                "title": "Persuasion",
                "pages": 249,
                "isbn": "9780141439518",
            },
            "Persuasion has 249 pages.",
            id="printed",
        ),
        pytest.param(
            {"__typename": "Audiobook", "title": "Emma", "duration": 723},
            "Emma lasts 723 minutes.",
            id="an audiobook",
        ),
        pytest.param(None, "No such publication.", id="missing"),
    ],
)
def test_telling_a_publication_s_length(
    publication: object, description: str, bookshop_server: _StartServer
) -> None:
    bookshop_server(_answering(lambda _request: {"data": {"publication": publication}}))

    assert length("p1") == description


@pytest.mark.parametrize(
    ("publication", "description"),
    [
        pytest.param(
            {"__typename": "Magazine", "title": "The Economist", "pages": 80},
            "The Economist has 80 pages.",
            id="printed",
        ),
        pytest.param({"__typename": "Audiobook"}, "No pages.", id="an audiobook"),
    ],
)
def test_counting_a_publication_s_pages(
    publication: object, description: str, bookshop_server: _StartServer
) -> None:
    bookshop_server(_answering(lambda _request: {"data": {"publication": publication}}))

    assert pages("p1") == description


@pytest.mark.parametrize(
    ("status", "label"),
    [
        pytest.param("SHIPPED", "On its way", id="a member known at codegen"),
        pytest.param(
            "OUT_FOR_DELIVERY", "Out for delivery", id="a member added after codegen"
        ),
    ],
)
def test_labeling_an_order_status(status: str, label: str) -> None:
    assert status_label(cast(OrderStatus, status)) == label


@pytest.mark.parametrize(
    ("genres", "first", "with_genres", "book", "label", "variables"),
    [
        pytest.param(
            (),
            None,
            None,
            _BOOK,
            "Persuasion",
            {"genres": []},
            id="defaults",
        ),
        pytest.param(
            ("FICTION",),
            3,
            True,
            {**_BOOK, "genre": "FICTION"},
            "Persuasion (fiction)",
            {"genres": ["FICTION"], "withGenres": True, "first": 3},
            id="a genre, a count, and genres shown",
        ),
    ],
)
def test_listing_titles_sends_only_the_given_count(
    *,
    genres: tuple[Genre, ...],
    first: int | None,
    with_genres: bool | None,
    book: object,
    label: str,
    variables: object,
    bookshop_server: _StartServer,
) -> None:
    requests: list[Mapping[str, object]] = []

    def respond(request: Mapping[str, object], /) -> object:
        requests.append(request)
        return {"data": {"books": [book]}}

    bookshop_server(_answering(respond))

    assert titles(*genres, first=first, with_genres=with_genres) == [label]
    assert [request["variables"] for request in requests] == [variables]


def test_the_catalog_lists_cards(bookshop_server: _StartServer) -> None:
    bookshop_server(
        _answering(
            lambda _request: {"data": {"books": [{**_BOOK, "genre": "FICTION"}]}}
        )
    )

    assert catalog(1) == ["Persuasion, by Jane Austen"]


def test_a_price_goes_out_and_comes_back_through_its_codec(
    bookshop_server: _StartServer,
) -> None:
    requests: list[Mapping[str, object]] = []

    def respond(request: Mapping[str, object], /) -> object:
        requests.append(request)
        return {"data": {"books": [{**_BOOK, "price": "8.99", "genre": "FICTION"}]}}

    bookshop_server(_answering(respond))

    assert cheaper_than(Decimal("10.00")) == ["Persuasion, by Jane Austen: 8.99"]
    assert [request["variables"] for request in requests] == [
        {"filter": {"priceBelow": "10.00"}}
    ]


@pytest.mark.usefixtures("fixed_key")
def test_an_order_gets_its_idempotency_key_injected(
    bookshop_server: _StartServer,
) -> None:
    requests: list[Mapping[str, object]] = []

    def respond(request: Mapping[str, object], /) -> object:
        requests.append(request)
        return {
            "data": {
                "placeOrder": {
                    **_ORDER,
                    "status": "PENDING",
                    "total": "8.99",
                    "placedAt": "2026-09-26T14:05:00+02:00",
                }
            }
        }

    address: Address = {
        "street": "1 Main Street",
        "city": "Bath",
        "postalCode": "BA1",
        "country": "GB",
    }
    bookshop_server(_answering(respond))

    assert order("1", address) == "Order o1: 8.99 at 14:05."
    ((request),) = requests
    assert request["variables"] == {
        "input": {
            "lines": [{"book": "1"}],
            "shippingAddress": address,
            "idempotencyKey": str(_KEY),
        }
    }


def test_listing_orders_names_the_customer_served(
    bookshop_server: _StartServer,
) -> None:
    requests: list[Mapping[str, object]] = []

    def respond(request: Mapping[str, object], /) -> object:
        requests.append(request)
        return {"data": {"orders": [_ORDER]}}

    bookshop_server(_answering(respond))
    token = bookshop.injectors.CURRENT_CUSTOMER.set("c1")

    try:
        assert order_ids() == ["o1"]
    finally:
        bookshop.injectors.CURRENT_CUSTOMER.reset(token)

    assert order_ids() == ["o1"]
    logged_in, guest = requests
    assert logged_in["variables"] == {"customer": "c1"}
    assert guest["variables"] == {}


@pytest.mark.usefixtures("fixed_key")
def test_canceling_orders_reports_each_failure(bookshop_server: _StartServer) -> None:
    canceled = {"id": "o1", "status": "CANCELED"}
    failing = {
        "data": {"cancelOrder_0": canceled, "cancelOrder_1": None},
        "errors": [{"message": "Already shipped.", "path": ["cancelOrder_1"]}],
    }
    variables = {"input": {"order": "o2", "idempotencyKey": str(_KEY)}}
    bookshop_server(
        _answering(
            lambda request: (
                failing
                if request["operationName"] == "MergedOperation"
                else {"data": {"cancelOrder": canceled}}
            )
        )
    )

    assert cancel(["o1", "o2"]) == [
        f"Raised by `CancelOrder` with variables {variables!r}. Already shipped.",
    ]
    assert cancel(["o1"]) == []


@pytest.mark.parametrize(
    ("response", "description"),
    [
        pytest.param({"data": {"order": _ORDER}}, "Order o1 is SHIPPED.", id="loaded"),
        pytest.param({"data": {"order": None}}, "No such order.", id="missing"),
        pytest.param(
            {
                "data": {"order": None},
                "errors": [
                    {"message": "Tracking is down.", "path": ["order", "status"]}
                ],
            },
            "Partially loaded: {'order': None} (Tracking is down.).",
            id="partially loaded",
        ),
    ],
)
def test_tracking_an_order(
    response: object, description: str, bookshop_server: _StartServer
) -> None:
    bookshop_server(_answering(lambda _request: response))

    assert track("o1") == description


def test_the_quickstart_prints_an_order(
    bookshop_server: _StartServer, capsys: pytest.CaptureFixture[str]
) -> None:
    def respond(request: Mapping[str, object], /) -> object:
        assert request["variables"] == {"id": "o1"}
        return {"data": {"order": _ORDER}}

    bookshop_server(_answering(respond))
    runpy.run_module("bookshop.quickstart")

    captured = capsys.readouterr()

    assert captured.out == "Order o1 is SHIPPED.\n"

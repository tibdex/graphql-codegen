import json
import runpy
from asyncio import run as run_async
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Final, cast
from uuid import UUID

import httpx2
import pytest

import bookshop.async_transport
import bookshop.transport
from bookshop.app import (
    book_and_similar,
    cancel,
    cheaper_than,
    describe,
    length,
    look_up,
    order,
    run_saved_search,
    status_label,
    track,
)
from bookshop.app_graphql import OnOrderStatusChanged
from bookshop.async_app import title
from bookshop.client.injection import injectors
from bookshop.client.runtime import RequestError
from bookshop.client.schema import Address, OrderStatus
from bookshop.get_order_graphql import GetOrder
from bookshop.scalar import ISBN
from bookshop.transport import Client
from tests._bookshop import BOOKSHOP_DIRECTORY

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


def test_the_bookshop_type_checks(assert_type_checks: Callable[[Path], None]) -> None:
    """Its client, and the code using it as a user's would."""
    assert_type_checks(BOOKSHOP_DIRECTORY)


@pytest.fixture(name="mocked_http")
def mocked_http_fixture(monkeypatch: pytest.MonkeyPatch) -> Callable[[_Handle], None]:
    """Make the bookshop's httpx2 client answer with the given handler instead of reaching a server."""

    def mock(handle: _Handle, /) -> None:
        monkeypatch.setattr(
            bookshop.async_transport,
            "http",
            httpx2.AsyncClient(
                transport=httpx2.MockTransport(handle),
                base_url="https://bookshop.example",
            ),
        )

    return mock


def _events(*statuses: str) -> httpx2.Response:
    """A stream of the order's statuses, then its completion, as a GraphQL over Server-Sent Events server sends it."""
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
    book_title = run_async(
        title(
            ISBN("9780141439518"),
            client=bookshop.async_transport.AsyncClient(
                bookshop.async_transport.transport
            ),
        )
    )

    assert book_title == "Persuasion"


def test_an_http_error_is_httpx2_s_own(mocked_http: Callable[[_Handle], None]) -> None:
    mocked_http(
        lambda _request: httpx2.Response(502, content=b"<html>Bad Gateway</html>")
    )

    with pytest.raises(httpx2.HTTPStatusError) as error_info:
        run_async(
            bookshop.async_transport.AsyncClient(bookshop.async_transport.transport)(
                GetOrder({"id": "o1"})
            )
        )

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
        run_async(
            bookshop.async_transport.AsyncClient(bookshop.async_transport.transport)(
                GetOrder({"id": "o1"})
            )
        )


def test_a_subscription_goes_over_httpx2(
    mocked_http: Callable[[_Handle], None],
) -> None:
    mocked_http(lambda _request: _events("SHIPPED", "DELIVERED"))

    async def statuses() -> list[str]:
        events = bookshop.async_transport.AsyncSubscriptionClient(
            bookshop.async_transport.subscription_transport
        )(OnOrderStatusChanged({"orderId": "o1"}))
        return [event["orderStatusChanged"]["status"] async for event in events]

    assert run_async(statuses()) == ["SHIPPED", "DELIVERED"]


def _client(respond: Callable[[Mapping[str, object]], object], /) -> Client:
    """A client whose transport answers each request with what *respond* returns for it."""

    def transport(body: bytes, /, *, timeout: float | None = None) -> bytes:
        return json.dumps(respond(json.loads(body))).encode()

    return Client(transport, injectors=injectors({"idempotencyKey": lambda: _KEY}))


def test_describing_a_book() -> None:
    assert (
        describe(
            ISBN("9780141439518"),
            client=_client(lambda _request: {"data": {"book": _BOOK}}),
        )
        == "Persuasion, by Jane Austen, costs 8.99."
    )


def test_describing_a_missing_book() -> None:
    assert (
        describe(
            ISBN("9780000000000"),
            client=_client(lambda _request: {"data": {"book": None}}),
        )
        == "No such book."
    )


def test_a_book_and_similar_publications_come_in_one_request() -> None:
    def respond(request: Mapping[str, object], /) -> object:
        assert request["operationName"] == "MergedOperation"
        search = [
            {"__typename": "Book", "title": "Emma", "price": "7.99"},
            {"__typename": "Author", "name": "Jane Austen"},
        ]
        return {"data": {"book_0": _BOOK, "search_1": search}}

    assert book_and_similar(
        ISBN("9780141439518"), "Austen", client=_client(respond)
    ) == (
        "Persuasion",
        ["Emma", "Jane Austen"],
    )


def test_books_and_publications_are_looked_up_in_one_request() -> None:
    def respond(request: Mapping[str, object], /) -> object:
        assert request["operationName"] == "MergedOperation"
        publication = {"__typename": "Audiobook", "title": "Emma", "duration": 723}
        return {"data": {"book_0": _BOOK, "publication_1": publication}}

    results = look_up([ISBN("9780141439518")], ["p1"], client=_client(respond))

    assert [list(data) for data in results] == [["book"], ["publication"]]


def test_a_saved_search_runs_with_its_filter_as_saved() -> None:
    saved = {"and": [{"genre": "FICTION"}, {"not": {"author": "a1"}}]}
    requests: list[Mapping[str, object]] = []

    def respond(request: Mapping[str, object], /) -> object:
        requests.append(request)
        return (
            {"data": {"savedSearch": {"value": saved}}}
            if request["operationName"] == "GetSavedSearch"
            else {"data": {"books": [{**_BOOK, "genre": "FICTION"}]}}
        )

    titles = run_saved_search("classics", client=_client(respond))

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
def test_telling_a_publication_s_length(publication: object, description: str) -> None:
    response = {"data": {"publication": publication}}

    assert length("p1", client=_client(lambda _request: response)) == description


@pytest.mark.parametrize(
    ("status", "label"),
    [
        pytest.param("SHIPPED", "On its way", id="a member the client knows"),
        pytest.param(
            "OUT_FOR_DELIVERY",
            "Out for delivery",
            id="a member added after generation",
        ),
    ],
)
def test_labeling_an_order_status(status: str, label: str) -> None:
    assert status_label(cast(OrderStatus, status)) == label


def test_a_price_goes_out_and_comes_back_through_its_codec() -> None:
    requests: list[Mapping[str, object]] = []

    def respond(request: Mapping[str, object], /) -> object:
        requests.append(request)
        return {"data": {"books": [{**_BOOK, "price": "8.99", "genre": "FICTION"}]}}

    assert cheaper_than(Decimal("10.00"), client=_client(respond)) == [
        "Persuasion: 8.99"
    ]
    assert [request["variables"] for request in requests] == [
        {"filter": {"priceBelow": "10.00"}}
    ]


def test_an_order_gets_its_idempotency_key_injected() -> None:
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

    assert order("1", address, client=_client(respond)) == "Order o1: 8.99 at 14:05."
    ((request),) = requests
    assert request["variables"] == {
        "input": {
            "lines": [{"book": "1"}],
            "shippingAddress": address,
            "idempotencyKey": str(_KEY),
        }
    }


def test_canceling_orders_reports_each_failure() -> None:
    canceled = {"id": "o1", "status": "CANCELED"}
    failing = {
        "data": {"cancelOrder_0": canceled, "cancelOrder_1": None},
        "errors": [{"message": "Already shipped.", "path": ["cancelOrder_1"]}],
    }

    variables = {"input": {"order": "o2", "idempotencyKey": str(_KEY)}}

    assert cancel(["o1", "o2"], client=_client(lambda _request: failing)) == [
        f"Raised by `CancelOrder` with variables {variables!r}. Already shipped.",
    ]
    assert (
        cancel(
            ["o1"],
            client=_client(lambda _request: {"data": {"cancelOrder": canceled}}),
        )
        == []
    )


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
def test_tracking_an_order(response: object, description: str) -> None:
    assert track("o1", client=_client(lambda _request: response)) == description


def test_the_quickstart_prints_an_order(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The README's first example."""

    def transport(body: bytes, /, *, timeout: float | None = None) -> bytes:
        assert json.loads(body)["variables"] == {"id": "o1"}
        return json.dumps({"data": {"order": _ORDER}}).encode()

    monkeypatch.setattr(bookshop.transport, "transport", transport)
    runpy.run_module("bookshop.quickstart")

    captured = capsys.readouterr()

    assert captured.out == "Order o1 is SHIPPED.\n"

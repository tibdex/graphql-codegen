import json
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing
from http.server import BaseHTTPRequestHandler
from threading import Event
from typing import Final
from urllib.error import HTTPError
from uuid import UUID

import pytest

import bookshop.transport
from bookshop.app import watch
from bookshop.app_graphql import GetBook, OnOrderStatusChanged, PlaceOrder
from bookshop.client.injection import injectors
from bookshop.client.runtime import Client, RequestError, SubscriptionClient
from bookshop.client.schema import Address
from bookshop.scalar import ISBN
from tests._server import PostHandler, serve

_GET_BOOK: Final = GetBook({"lookup": {"isbn": ISBN("9780141439518")}})
_ON_STATUS_CHANGED: Final = OnOrderStatusChanged({"orderId": "o1"})
_BOOK: Final = {
    "id": "1",
    "title": "Persuasion",
    "price": "8.99",
    "author": None,
    "isbn": "9780141439518",
    "pageCount": 249,
}


def _status_event(status: str, /, *, indent: int | None = None) -> str:
    """An event whose data spans one line per line of its JSON, which *indent* multiplies."""
    data = {"data": {"orderStatusChanged": {"id": "o1", "status": status}}}
    lines = json.dumps(data, indent=indent).splitlines()
    return "event: next\n" + "".join(f"data: {line}\n" for line in lines) + "\n"


def _read_body(handler: BaseHTTPRequestHandler, /) -> bytes:
    return handler.rfile.read(int(handler.headers["Content-Length"]))


def _start_stream(handler: BaseHTTPRequestHandler, /) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()


@pytest.fixture(name="bookshop_server")
def bookshop_server_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[PostHandler], None]]:
    """Point the bookshop's transports at a real server on a local socket, handling requests as told, until the test ends: what a mocked transport cannot show."""
    with ExitStack() as stack:

        def start(post_handler: PostHandler, /) -> None:
            url = stack.enter_context(serve(post_handler))
            monkeypatch.setattr(bookshop.transport, "BASE_URL", url)

        yield start


def test_a_call_goes_over_a_socket_and_its_response_comes_back(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    received: list[tuple[str, bytes]] = []

    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        received.append((handler.headers["Content-Type"], _read_body(handler)))
        handler.send_response(200)
        handler.end_headers()
        handler.wfile.write(json.dumps({"data": {"book": _BOOK}}).encode())

    bookshop_server(handle)
    data = Client(bookshop.transport.transport)(_GET_BOOK)

    assert data["book"] is not None
    assert data["book"]["title"] == "Persuasion"

    ((content_type, body),) = received
    assert content_type == "application/json"
    assert json.loads(body)["variables"] == {"lookup": {"isbn": "9780141439518"}}


def test_a_dropped_connection_sends_the_same_body_again(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    bodies: list[bytes] = []

    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        bodies.append(_read_body(handler))

        # The order is placed, but its response is lost.
        if len(bodies) == 1:
            handler.close_connection = True
            return

        placed = {
            "id": "o1",
            "status": "PENDING",
            "total": "8.99",
            "placedAt": "2026-09-26T14:05:00+02:00",
        }
        handler.send_response(200)
        handler.end_headers()
        handler.wfile.write(json.dumps({"data": {"placeOrder": placed}}).encode())

    bookshop_server(handle)
    client = Client(
        bookshop.transport.transport,
        injectors=injectors({"idempotencyKey": lambda: UUID(int=len(bodies))}),
    )
    address: Address = {
        "street": "1 Main St",
        "city": "Bath",
        "postalCode": "BA1",
        "country": "UK",
    }
    data = client(
        PlaceOrder({"input": {"lines": [{"book": "1"}], "shippingAddress": address}})
    )

    assert data["placeOrder"]["id"] == "o1"

    first, second = bodies
    assert first == second


def test_a_timeout_reaches_the_socket(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    def handle(_handler: BaseHTTPRequestHandler, /) -> None:
        Event().wait(0.5)

    bookshop_server(handle)

    with pytest.raises(TimeoutError):
        Client(bookshop.transport.transport)(_GET_BOOK, timeout=0.05)


def test_an_http_error_is_urllib_s_own(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        # Read first: closing with the request unread resets the connection, which may lose the response.
        _read_body(handler)
        handler.send_response(503)
        handler.end_headers()
        handler.wfile.write(b"Try later.")

    bookshop_server(handle)

    with pytest.raises(HTTPError) as error_info:
        Client(bookshop.transport.transport)(_GET_BOOK)

    with error_info.value as error:
        assert (error.code, error.read()) == (503, b"Try later.")


def test_a_request_error_is_read_whatever_its_status(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        # Read first: closing with the request unread resets the connection, which may lose the response.
        _read_body(handler)
        handler.send_response(400)
        handler.send_header("Content-Type", "application/graphql-response+json")
        handler.end_headers()
        handler.wfile.write(b'{"errors": [{"message": "Syntax Error."}]}')

    bookshop_server(handle)

    with pytest.raises(RequestError, match="Syntax Error"):
        Client(bookshop.transport.transport)(_GET_BOOK)


def test_a_subscription_yields_each_event_until_the_server_completes_it(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    """A comment, and an event whose data spans two lines, as the protocol allows, and nothing read after `complete`."""

    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        _start_stream(handler)
        handler.wfile.write(
            (
                ": keep-alive\n\n"
                + _status_event("SHIPPED")
                + _status_event("DELIVERED", indent=2)
                + "event: complete\ndata:\n\n"
                + _status_event("CANCELED")
            ).encode()
        )

    bookshop_server(handle)
    events = SubscriptionClient(bookshop.transport.subscription_transport)(
        _ON_STATUS_CHANGED
    )

    assert [event["orderStatusChanged"]["status"] for event in events] == [
        "SHIPPED",
        "DELIVERED",
    ]


def test_closing_a_subscription_closes_the_connection(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    disconnected = Event()

    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        _start_stream(handler)

        try:
            # Bounded, so that a failure to unsubscribe ends the thread too.
            for _ in range(500):
                handler.wfile.write(_status_event("SHIPPED").encode())
                handler.wfile.flush()
                Event().wait(0.01)
        except OSError:
            disconnected.set()

    bookshop_server(handle)

    with closing(
        SubscriptionClient(bookshop.transport.subscription_transport)(
            _ON_STATUS_CHANGED
        )
    ) as events:
        next(events)
        assert not disconnected.is_set()

    assert disconnected.wait(5)


@pytest.mark.parametrize(
    ("stream", "statuses"),
    [
        pytest.param(
            _status_event("SHIPPED")
            + _status_event("DELIVERED")
            + _status_event("CANCELED"),
            ["SHIPPED", "DELIVERED"],
            id="until the order is delivered",
        ),
        pytest.param(
            _status_event("SHIPPED") + "event: complete\ndata:\n\n",
            ["SHIPPED"],
            id="until the server completes",
        ),
    ],
)
def test_watching_an_order_follows_its_statuses(
    stream: str, statuses: list[str], bookshop_server: Callable[[PostHandler], None]
) -> None:
    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        _start_stream(handler)
        handler.wfile.write(stream.encode())

    bookshop_server(handle)

    statuses_seen = watch(
        "o1", client=SubscriptionClient(bookshop.transport.subscription_transport)
    )

    assert statuses_seen == statuses

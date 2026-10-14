import json
from collections.abc import Callable
from contextlib import closing
from http.server import BaseHTTPRequestHandler
from socket import SHUT_RDWR
from threading import Event
from typing import Final
from urllib.error import HTTPError

import pytest

import bookshop.transport
from bookshop.app import watch
from bookshop.app_graphql import GetBook, OnOrderStatusChanged
from bookshop.graphql.runtime import ExecutionError, RequestError
from bookshop.scalar import ISBN
from tests._server import PostHandler

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
    data = {"data": {"orderStatusChanged": {"id": "o1", "status": status}}}
    lines = json.dumps(data, indent=indent).splitlines()
    return "event: next\n" + "".join(f"data: {line}\n" for line in lines) + "\n"


def _read_body(handler: BaseHTTPRequestHandler, /) -> bytes:
    return handler.rfile.read(int(handler.headers["Content-Length"]))


def _start_stream(handler: BaseHTTPRequestHandler, /) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()


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
    data = bookshop.transport.send(_GET_BOOK)

    assert data["book"] is not None
    assert data["book"]["title"] == "Persuasion"

    ((content_type, body),) = received
    assert content_type == "application/json"
    assert json.loads(body)["variables"] == {"lookup": {"isbn": "9780141439518"}}


def test_a_dropped_connection_is_retried_with_the_same_body(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    received: list[bytes] = []

    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        received.append(_read_body(handler))

        if len(received) == 1:
            handler.connection.shutdown(SHUT_RDWR)
            return

        handler.send_response(200)
        handler.end_headers()
        handler.wfile.write(json.dumps({"data": {"book": _BOOK}}).encode())

    bookshop_server(handle)
    data = bookshop.transport.send(_GET_BOOK)

    assert data["book"]["title"] == "Persuasion"
    assert len(received) == 2
    assert received[0] == received[1]


def test_a_connection_dropping_every_time_gives_up(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    attempts = 0

    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        nonlocal attempts
        attempts += 1
        _read_body(handler)
        handler.connection.shutdown(SHUT_RDWR)

    bookshop_server(handle)

    with pytest.raises(ConnectionError):
        bookshop.transport.send(_GET_BOOK)

    assert attempts == 3


def test_a_timeout_reaches_the_socket(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    def handle(_handler: BaseHTTPRequestHandler, /) -> None:
        Event().wait(0.5)

    bookshop_server(handle)

    with pytest.raises(TimeoutError):
        bookshop.transport.send(_GET_BOOK, timeout=0.05)


def test_an_http_error_is_urllib_s_own(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        # Read the body first, or closing resets the connection and may lose the response.
        _read_body(handler)
        handler.send_response(503)
        handler.end_headers()
        handler.wfile.write(b"Try later.")

    bookshop_server(handle)

    with pytest.raises(HTTPError) as error_info:
        bookshop.transport.send(_GET_BOOK)

    with error_info.value as error:
        assert (error.code, error.read()) == (503, b"Try later.")


def test_a_request_error_is_read_whatever_its_status(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        # Read the body first, or closing resets the connection and may lose the response.
        _read_body(handler)
        handler.send_response(400)
        handler.send_header("Content-Type", "application/graphql-response+json")
        handler.end_headers()
        handler.wfile.write(b'{"errors": [{"message": "Syntax Error."}]}')

    bookshop_server(handle)

    with pytest.raises(RequestError, match="Syntax Error"):
        bookshop.transport.send(_GET_BOOK)


def test_a_subscription_yields_each_event_until_the_server_completes_it(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
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
    events = bookshop.transport.subscribe(_ON_STATUS_CHANGED)

    assert [event["orderStatusChanged"]["status"] for event in events] == [
        "SHIPPED",
        "DELIVERED",
    ]


def test_an_event_with_errors_raises_out_of_the_stream_and_closes_the_connection(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    disconnected = Event()

    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        _start_stream(handler)
        handler.wfile.write(
            (
                _status_event("SHIPPED")
                + 'event: next\ndata: {"data": null, "errors": [{"message": "Boom."}]}\n\n'
            ).encode()
        )

        try:
            # Bounded, so that a failed close still ends the thread.
            for _ in range(500):
                handler.wfile.write(_status_event("DELIVERED").encode())
                handler.wfile.flush()
                Event().wait(0.01)
        except OSError:
            disconnected.set()

    bookshop_server(handle)
    events = bookshop.transport.subscribe(_ON_STATUS_CHANGED)

    assert next(events)["orderStatusChanged"]["status"] == "SHIPPED"

    with pytest.raises(ExecutionError, match="Boom"):
        next(events)

    assert disconnected.wait(5)


def test_closing_a_subscription_closes_the_connection(
    bookshop_server: Callable[[PostHandler], None],
) -> None:
    disconnected = Event()

    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        _start_stream(handler)

        try:
            # Bounded, so that a failed unsubscribe still ends the thread.
            for _ in range(500):
                handler.wfile.write(_status_event("SHIPPED").encode())
                handler.wfile.flush()
                Event().wait(0.01)
        except OSError:
            disconnected.set()

    bookshop_server(handle)

    with closing(bookshop.transport.subscribe(_ON_STATUS_CHANGED)) as events:
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

    statuses_seen = watch("o1")

    assert statuses_seen == statuses

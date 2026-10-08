import inspect
import json
import shutil
import subprocess
import sys
from asyncio import run as run_async
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import aclosing, closing
from decimal import Decimal
from pathlib import Path
from types import EllipsisType
from typing import (
    Any,
    Final,
    Protocol,
    assert_type,
    get_args,
    get_origin,
    get_overloads,
)

import pytest

from bookshop.app_graphql import (
    CancelOrder,
    CancelOrderData,
    GetBook,
    GetBookData,
    ListBooks,
    OnOrderStatusChanged,
)
from bookshop.client.runtime import (
    AsyncClient,
    AsyncSubscriptionClient,
    Client,
    ExecutionError,
    Request,
    RequestError,
    SubscriptionClient,
    UnexpectedNullError,
)
from bookshop.scalar import ISBN

_CARD: Final = {
    "id": "1",
    "title": "Persuasion",
    "price": "8.99",
    "author": None,
}
_BOOK: Final = {**_CARD, "isbn": "9780141439518", "pageCount": 249}
_GET_BOOK: Final = GetBook({"lookup": {"isbn": ISBN("9780141439518")}})
_ON_STATUS_CHANGED: Final = OnOrderStatusChanged({"orderId": "o1"})


def _respond(payload: object, /) -> Callable[[bytes], bytes]:
    """A transport answering every request with *payload*."""
    return lambda _body: json.dumps(payload).encode()


def _status_event(status: str, /) -> bytes:
    return json.dumps(
        {"data": {"orderStatusChanged": {"id": "o1", "status": status}}}
    ).encode()


def test_a_request_is_a_value_that_runs_nothing() -> None:
    """Making it checks the variables before any side effect, and it runs as often as a client is called with it."""
    sent: list[bytes] = []

    def transport(body: bytes, /) -> bytes:
        sent.append(body)
        return json.dumps({"data": {"book": _BOOK}}).encode()

    assert isinstance(_GET_BOOK, Request)
    assert not sent

    client = Client(transport)

    assert client(_GET_BOOK) == client(_GET_BOOK)
    assert len(sent) == 2
    assert json.loads(sent[0])["variables"] == {"lookup": {"isbn": "9780141439518"}}


def test_transport_arguments_reach_the_transport_checked() -> None:
    """Unknown or mistyped arguments are type errors, as the unused-suppression checks prove, and a transport without parameters takes none."""
    timeouts: list[float | None] = []

    def transport(body: bytes, /, *, timeout: float | None = None) -> bytes:
        timeouts.append(timeout)
        return _respond({"data": {"books": []}})(body)

    async def async_transport(body: bytes, /, *, timeout: float | None = None) -> bytes:
        return transport(body, timeout=timeout)

    client = Client(transport)
    client(ListBooks({}), timeout=5.0)
    client(ListBooks({}))
    run_async(AsyncClient(async_transport)(ListBooks({}), timeout=1.0))

    assert timeouts == [5.0, None, 1.0]

    with pytest.raises(TypeError):
        client(ListBooks({}), timout=5.0)  # ty: ignore[no-matching-overload]
    client(ListBooks({}), timeout="slow")  # ty: ignore[no-matching-overload]

    with pytest.raises(TypeError):
        Client(_respond({"data": {"books": []}}))(ListBooks({}), timeout=5.0)  # ty: ignore[no-matching-overload]


def test_a_client_s_type_is_named_after_its_transport_s_signature() -> None:
    """A `Protocol` of the signature names it, parameters included, as the unused-suppression checks prove."""

    class Transport(Protocol):
        def __call__(self, body: bytes, /, *, timeout: float) -> bytes: ...

    def transport(body: bytes, /, *, timeout: float) -> bytes:
        return _respond({"data": {"books": []}})(body)

    client: Client[Transport] = Client(transport)
    client(ListBooks({}), timeout=5.0)

    with pytest.raises(TypeError):
        client(ListBooks({}))  # ty: ignore[no-matching-overload]

    # A client over a transport without the parameter is not one.
    other: Client[Transport] = Client(_respond({"data": {"books": []}}))  # ty: ignore[invalid-assignment]
    assert other is not client


def test_variables_and_operation_types_are_checked_statically() -> None:
    """As the unused-suppression checks prove: nothing is validated at runtime."""
    assert isinstance(GetBook({"lookup": 1}), Request)  # ty: ignore[invalid-argument-type]
    # A client returning one data does not take a subscription, nor a subscription client a query.
    Client(_respond({"data": {}}))(_ON_STATUS_CHANGED)  # ty: ignore[no-matching-overload]
    SubscriptionClient(lambda _body: iter([]))(ListBooks({}))  # ty: ignore[invalid-argument-type]


def test_an_unknown_enum_member_reaches_the_caller_untouched() -> None:
    """Enums are closed `Literal`s, but nothing checks it at runtime, so a newer server breaks nothing.

    A caller keeping a catch-all arm handles the member, and one keeping `assert_never` is told by the type checker the day the schema grows.
    """
    data = Client(_respond({"data": {"books": [{**_CARD, "genre": "COOKING"}]}}))(
        ListBooks({})
    )
    (book,) = data["books"]

    assert book["genre"] == "COOKING"


def test_a_sequence_of_operations_returns_a_tuple_of_their_common_data() -> None:
    """Synchronously or not, an empty sequence sending nothing."""
    canceled = {
        "data": {
            "cancelOrder_0": {"id": "o1", "status": "CANCELED"},
            "cancelOrder_1": None,
        }
    }
    cancellations = [
        CancelOrder({"input": {"order": "o1"}}),
        CancelOrder({"input": {"order": "o2"}}),
    ]

    async def async_transport(body: bytes, /) -> bytes:
        return _respond(canceled)(body)

    data = Client(_respond(canceled))(cancellations)

    assert_type(data, tuple[CancelOrderData, ...])
    assert data == (
        {"cancelOrder": {"id": "o1", "status": "CANCELED"}},
        {"cancelOrder": None},
    )
    assert run_async(AsyncClient(async_transport)(cancellations)) == data
    assert Client(_respond(None))([]) == ()


def test_a_query_and_a_mutation_cannot_share_a_request() -> None:
    """Not rejected statically by ty, hence at runtime."""
    with pytest.raises(ValueError, match="different types"):
        Client(_respond({"data": {}}))(
            (ListBooks({}), CancelOrder({"input": {"order": "o1"}}))
        )


def test_a_request_returning_its_error_gets_it_in_place_of_its_data() -> None:
    """Typed by the request, as `assert_type` proves, its parsed data included; an error meaning that the request as a whole failed still raises."""
    down = _respond(
        {"data": {"book": None}, "errors": [{"message": "Down.", "path": ["book"]}]}
    )
    result = Client(down)(_GET_BOOK.returning_error())

    assert_type(result, GetBookData | ExecutionError[GetBookData])
    assert isinstance(result, ExecutionError)
    assert_type(result.parse_data(), GetBookData | None)
    # `book` is `@nonNull`, so its null moves up to the data itself.
    assert (result.data, result.parse_data()) == ({"book": None}, None)
    assert result.__notes__ == [
        "Raised by `GetBook` with variables {'lookup': {'isbn': '9780141439518'}}."
    ]
    assert Client(_respond({"data": {"book": _BOOK}}))(_GET_BOOK.returning_error()) == {
        "book": {**_BOOK, "price": Decimal("8.99")}
    }

    with pytest.raises(RequestError):
        Client(_respond({"errors": [{"message": "Syntax Error"}]}))(
            _GET_BOOK.returning_error()
        )


def test_a_merge_returns_each_request_s_error_in_its_place_unless_one_raises() -> None:
    cancellations = (
        CancelOrder({"input": {"order": "o1"}}),
        CancelOrder({"input": {"order": "o2"}}),
    )
    one_failing = _respond(
        {
            "data": {
                "cancelOrder_0": {"id": "o1", "status": "CANCELED"},
                "cancelOrder_1": None,
            },
            "errors": [{"message": "Already shipped.", "path": ["cancelOrder_1"]}],
        },
    )
    first, second = Client(one_failing)(
        (cancellations[0].returning_error(), cancellations[1].returning_error())
    )

    assert_type(second, CancelOrderData | ExecutionError[CancelOrderData])
    assert first == {"cancelOrder": {"id": "o1", "status": "CANCELED"}}
    assert isinstance(second, ExecutionError)
    assert second.errors == ({"message": "Already shipped.", "path": ["cancelOrder"]},)

    both_failing = _respond(
        {
            "data": {"cancelOrder_0": None, "cancelOrder_1": None},
            "errors": [
                {"message": "Unknown.", "path": ["cancelOrder_0"]},
                {"message": "Already shipped.", "path": ["cancelOrder_1"]},
            ],
        },
    )

    with pytest.raises(ExceptionGroup) as group_info:
        Client(both_failing)((cancellations[0], cancellations[1].returning_error()))

    assert [str(error) for error in group_info.value.exceptions] == [
        "Unknown.",
        "Already shipped.",
    ]


def test_a_merge_s_errors_are_split_per_operation_unless_its_data_is_null() -> None:
    """Each error holds its operation's own errors, paths, and data, and a note naming it; `except*` catches the unsplit error too."""
    cancellations = [
        CancelOrder({"input": {"order": "o1"}}),
        CancelOrder({"input": {"order": "o2"}}),
    ]
    failing = _respond(
        {
            "data": {
                "cancelOrder_0": {"id": "o1", "status": "CANCELED"},
                "cancelOrder_1": None,
            },
            "errors": [{"message": "Already shipped.", "path": ["cancelOrder_1"]}],
        },
    )

    with pytest.raises(ExceptionGroup) as group_info:
        Client(failing)(cancellations)

    (error,) = group_info.value.exceptions
    assert isinstance(error, ExecutionError)
    assert error.errors == ({"message": "Already shipped.", "path": ["cancelOrder"]},)
    assert error.data == error.parse_data() == {"cancelOrder": None}
    assert error.__notes__ == [
        "Raised by `CancelOrder` with variables {'input': {'order': 'o2'}}."
    ]

    with pytest.raises(ExecutionError):
        Client(_respond({"data": None, "errors": [{"message": "Down."}]}))(
            cancellations
        )


@pytest.mark.parametrize(
    ("requests", "payload", "error_type", "note"),
    [
        pytest.param(
            _GET_BOOK,
            {"data": None, "errors": [{"message": "Down.", "path": ["book"]}]},
            ExecutionError,
            "Raised by `GetBook` with variables {'lookup': {'isbn': '9780141439518'}}.",
            id="an execution error",
        ),
        pytest.param(
            (ListBooks({}), _GET_BOOK),
            {"data": {"books_0": [], "book_1": None}},
            UnexpectedNullError,
            "Raised by `GetBook` with variables {'lookup': {'isbn': '9780141439518'}}.",
            id="an unexpected null in a merge",
        ),
    ],
)
def test_an_error_reading_a_response_notes_its_operation_and_the_variables_sent(
    requests: Any, payload: object, error_type: type[Exception], note: str
) -> None:
    with pytest.raises(error_type) as error_info:
        Client(_respond(payload))(requests)

    assert error_info.value.__notes__ == [note]


def _longest_typed_tuple(client: type[Client] | type[AsyncClient], /) -> int:
    """The most operations a client types position by position, read off its overloads."""
    return max(
        len(get_args(returned))
        for overload in get_overloads(client.__call__)
        if get_origin(returned := inspect.signature(overload).return_annotation)
        is tuple
        and not any(
            isinstance(argument, EllipsisType) for argument in get_args(returned)
        )
    )


def test_the_clients_type_as_many_operations_as_asyncio_gather(tmp_path: Path) -> None:
    """Typeshed's limit for `asyncio.gather()`, the closest analogue, rather than one of ours.

    Read off the stubs ty vendors, since `asyncio.gather()` has no overloads at runtime: each arity is a call of its own, whose line in ty's report tells it apart.
    """
    ty = shutil.which("ty", path=str(Path(sys.executable).parent))
    assert ty is not None
    header = [
        "import asyncio",
        "from typing import reveal_type",
        "",
        "async def value() -> int: ...",
        "",
        "async def main() -> None:",
    ]
    calls = [
        f"    reveal_type(asyncio.gather({', '.join(['value()'] * arity)}))"
        for arity in range(1, 13)
    ]
    probe = tmp_path / "probe.py"
    probe.write_text("\n".join([*header, *calls, ""]), encoding="utf-8")
    report = json.loads(
        subprocess.run(
            [ty, "check", "--output-format", "gitlab", str(probe)],
            capture_output=True,
            check=False,
            cwd=tmp_path,
            encoding="utf-8",
        ).stdout,
    )
    typed_arities = [
        diagnostic["location"]["positions"]["begin"]["line"] - len(header)
        for diagnostic in report
        if diagnostic["check_name"] == "revealed-type"
        and "tuple[" in diagnostic["description"]
    ]

    assert {_longest_typed_tuple(Client), _longest_typed_tuple(AsyncClient)} == {
        max(typed_arities)
    }


def test_a_subscription_returning_its_errors_streams_past_them() -> None:
    def subscription_transport(_body: bytes, /) -> Generator[bytes, None, None]:
        yield json.dumps({"data": None, "errors": [{"message": "Lagging."}]}).encode()
        yield _status_event("DELIVERED")

    lagging, delivered = SubscriptionClient(subscription_transport)(
        _ON_STATUS_CHANGED.returning_error()
    )

    assert isinstance(lagging, ExecutionError)
    assert (str(lagging), lagging.parse_data()) == ("Lagging.", None)
    assert delivered == {"orderStatusChanged": {"id": "o1", "status": "DELIVERED"}}


def test_closing_a_subscription_unsubscribes() -> None:
    """The transport's cleanup runs when the caller stops listening, not only when the server stops."""
    unsubscribed: list[str] = []

    def subscription_transport(_body: bytes, /) -> Generator[bytes, None, None]:
        try:
            while True:
                yield _status_event("SHIPPED")
        finally:
            unsubscribed.append("sync")

    async def async_subscription_transport(
        _body: bytes, /
    ) -> AsyncGenerator[bytes, None]:
        try:
            while True:
                yield _status_event("SHIPPED")
        finally:
            unsubscribed.append("async")

    async def listen_once() -> None:
        async with aclosing(
            AsyncSubscriptionClient(async_subscription_transport)(_ON_STATUS_CHANGED)
        ) as events:
            async for _event in events:
                break

    with closing(
        SubscriptionClient(subscription_transport)(_ON_STATUS_CHANGED)
    ) as events:
        next(events)
        assert not unsubscribed

    run_async(listen_once())

    assert unsubscribed == ["sync", "async"]


def test_a_response_with_errors_raises_out_of_the_stream_and_unsubscribes() -> None:
    unsubscribed: list[bool] = []

    def subscription_transport(_body: bytes, /) -> Generator[bytes, None, None]:
        try:
            yield _status_event("SHIPPED")
            yield b'{"data": null, "errors": [{"message": "Boom."}]}'
            yield _status_event("DELIVERED")
        finally:
            unsubscribed.append(True)

    events = SubscriptionClient(subscription_transport)(_ON_STATUS_CHANGED)

    event = next(events)

    assert event["orderStatusChanged"]["status"] == "SHIPPED"

    with pytest.raises(ExecutionError, match="Boom"):
        next(events)

    assert unsubscribed == [True]

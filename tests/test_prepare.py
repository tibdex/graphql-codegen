import json
from decimal import Decimal
from typing import Any, Final, assert_type
from uuid import UUID

import pytest

import bookshop.transport
from bookshop.app_graphql import (
    CancelOrder,
    CancelOrderData,
    GetBook,
    GetBookData,
    ListBooks,
    OnOrderStatusChanged,
)
from bookshop.graphql.injection import injectors
from bookshop.graphql.runtime import (
    OMITTED,
    ExecutionError,
    Request,
    RequestError,
    UnexpectedNullError,
    merge,
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
_KEY: Final = UUID(int=0)
_INJECTORS: Final = injectors(
    {"idempotencyKey": lambda: _KEY, "customer": lambda: OMITTED}
)


def _parse[Data](request: Request[Any, Data], payload: object, /) -> Data:
    return request.prepare(injectors=_INJECTORS).parse(json.dumps(payload).encode())


def _status_event(status: str, /) -> bytes:
    return json.dumps(
        {"data": {"orderStatusChanged": {"id": "o1", "status": status}}}
    ).encode()


def test_creating_a_request_has_no_side_effect() -> None:
    assert isinstance(_GET_BOOK, Request)
    prepared = _GET_BOOK.prepare(injectors=_INJECTORS)

    assert bytes(prepared) == bytes(_GET_BOOK.prepare(injectors=_INJECTORS))
    assert json.loads(bytes(prepared))["variables"] == {
        "lookup": {"isbn": "9780141439518"}
    }


def test_a_misuse_is_caught_by_the_type_checker_alone() -> None:
    # Each suppression is checked as used: nothing is validated at runtime.
    assert isinstance(GetBook({"lookup": 1}), Request)  # ty: ignore[invalid-argument-type]
    # A merge takes one operation type, never a subscription, and a subscription reader takes no query.
    assert isinstance(merge(ListBooks({}), _ON_STATUS_CHANGED), Request)  # ty: ignore[invalid-argument-type]
    assert isinstance(
        merge(ListBooks({}), CancelOrder({"input": {"order": "o1"}})),  # ty: ignore[invalid-argument-type]
        Request,
    )
    # A generator, so nothing runs.
    bookshop.transport.subscribe(ListBooks({}))  # ty: ignore[invalid-argument-type]
    # Every injector is required.
    injectors({})  # ty: ignore[invalid-argument-type, missing-typed-dict-key]
    # A merge has no error of its own to return: each merged request returns or raises its own.
    assert not hasattr(merge(ListBooks({}), ListBooks({})), "returning_error")


def test_an_unknown_enum_member_reaches_the_caller_untouched() -> None:
    data = _parse(ListBooks({}), {"data": {"books": [{**_CARD, "genre": "COOKING"}]}})
    (book,) = data["books"]

    assert book["genre"] == "COOKING"


def test_a_merge_is_a_request_and_a_sequence_merges_into_a_tuple_of_its_common_data() -> (
    None
):
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
    merged = merge(cancellations)

    assert isinstance(merged, Request)
    assert repr(merged) == f"merge({cancellations[0]!r}, {cancellations[1]!r})"

    data = _parse(merged, canceled)

    assert_type(data, tuple[CancelOrderData, ...])
    assert data == (
        {"cancelOrder": {"id": "o1", "status": "CANCELED"}},
        {"cancelOrder": None},
    )

    with pytest.raises(ValueError, match="Cannot merge no request"):
        merge([])


def test_an_operation_injecting_a_value_cannot_be_prepared_without_injectors() -> None:
    with pytest.raises(
        ValueError, match="without injectors: it injects customer, idempotencyKey"
    ):
        CancelOrder({"input": {"order": "o1"}}).prepare()


def test_a_request_returning_its_error_gets_it_in_place_of_its_data() -> None:
    down = {"data": {"book": None}, "errors": [{"message": "Down.", "path": ["book"]}]}
    result = _parse(_GET_BOOK.returning_error(), down)

    assert_type(result, GetBookData | ExecutionError[GetBookData])
    assert isinstance(result, ExecutionError)
    assert_type(result.parse_data(), GetBookData | None)
    # `book` is `@nonNull`, so its null moves up to the data itself.
    assert (result.data, result.parse_data()) == ({"book": None}, None)
    assert result.__notes__ == [
        "Raised by `GetBook` with variables {'lookup': {'isbn': '9780141439518'}}."
    ]
    assert _parse(_GET_BOOK.returning_error(), {"data": {"book": _BOOK}}) == {
        "book": {**_BOOK, "price": Decimal("8.99")}
    }

    with pytest.raises(RequestError):
        _parse(_GET_BOOK.returning_error(), {"errors": [{"message": "Syntax Error"}]})


def test_a_merge_returns_each_request_s_error_in_its_place_unless_one_raises() -> None:
    cancellations = (
        CancelOrder({"input": {"order": "o1"}}),
        CancelOrder({"input": {"order": "o2"}}),
    )
    one_failing = {
        "data": {
            "cancelOrder_0": {"id": "o1", "status": "CANCELED"},
            "cancelOrder_1": None,
        },
        "errors": [{"message": "Already shipped.", "path": ["cancelOrder_1"]}],
    }
    first, second = _parse(
        merge(cancellations[0].returning_error(), cancellations[1].returning_error()),
        one_failing,
    )

    assert_type(second, CancelOrderData | ExecutionError[CancelOrderData])
    assert first == {"cancelOrder": {"id": "o1", "status": "CANCELED"}}
    assert isinstance(second, ExecutionError)
    assert second.errors == ({"message": "Already shipped.", "path": ["cancelOrder"]},)

    both_failing = {
        "data": {"cancelOrder_0": None, "cancelOrder_1": None},
        "errors": [
            {"message": "Unknown.", "path": ["cancelOrder_0"]},
            {"message": "Already shipped.", "path": ["cancelOrder_1"]},
        ],
    }

    with pytest.raises(ExceptionGroup) as group_info:
        _parse(
            merge(cancellations[0], cancellations[1].returning_error()), both_failing
        )

    assert [str(error) for error in group_info.value.exceptions] == [
        "Unknown.",
        "Already shipped.",
    ]


def test_a_merge_s_errors_are_split_per_operation_unless_its_data_is_null() -> None:
    cancellations = merge(
        [
            CancelOrder({"input": {"order": "o1"}}),
            CancelOrder({"input": {"order": "o2"}}),
        ]
    )
    failing = {
        "data": {
            "cancelOrder_0": {"id": "o1", "status": "CANCELED"},
            "cancelOrder_1": None,
        },
        "errors": [{"message": "Already shipped.", "path": ["cancelOrder_1"]}],
    }

    with pytest.raises(ExceptionGroup) as group_info:
        _parse(cancellations, failing)

    (error,) = group_info.value.exceptions
    assert isinstance(error, ExecutionError)
    assert error.errors == ({"message": "Already shipped.", "path": ["cancelOrder"]},)
    assert error.data == error.parse_data() == {"cancelOrder": None}
    assert error.__notes__ == [
        f"Raised by `CancelOrder` with variables {{'input': {{'order': 'o2', 'idempotencyKey': '{_KEY}'}}}}."
    ]

    with pytest.raises(ExecutionError):
        _parse(cancellations, {"data": None, "errors": [{"message": "Down."}]})


def test_an_unexpected_null_in_a_merge_notes_its_operation_and_the_variables_sent() -> (
    None
):
    with pytest.raises(UnexpectedNullError) as error_info:
        _parse(
            merge(ListBooks({}), _GET_BOOK), {"data": {"books_0": [], "book_1": None}}
        )

    assert error_info.value.__notes__ == [
        "Raised by `GetBook` with variables {'lookup': {'isbn': '9780141439518'}}."
    ]


def test_a_subscription_returning_its_errors_parses_past_them() -> None:
    prepared = _ON_STATUS_CHANGED.returning_error().prepare(injectors=_INJECTORS)
    lagging = prepared.parse(
        json.dumps({"data": None, "errors": [{"message": "Lagging."}]}).encode()
    )
    delivered = prepared.parse(_status_event("DELIVERED"))

    assert isinstance(lagging, ExecutionError)
    assert (str(lagging), lagging.parse_data()) == ("Lagging.", None)
    assert delivered == {"orderStatusChanged": {"id": "o1", "status": "DELIVERED"}}

import json
from collections.abc import Callable
from typing import Annotated, Final, Literal

import pytest

from graphql_codegen.runtime import (
    OMITTED,
    ExecutionError,
    Operation,
    ProtocolError,
    RequestError,
)
from graphql_codegen.runtime._compat import TypedDict
from graphql_codegen.runtime._prepare import (
    parse_data,
    parse_response,
    prepare,
    resolve_variables,
)
from graphql_codegen.runtime._reflection import NON_NULL
from graphql_codegen.runtime.injection import _Injectors

_LIST_BOOKS: Final[
    Operation[Literal["query"], dict[str, object], dict[str, object]]
] = Operation(
    operation_type="query",
    name="ListBooks",
    document="query ListBooks { books { title } }",
    variables_type=dict[str, object],
    data_type=dict[str, object],
)


def test_the_request_prefix_is_serialized_once() -> None:
    assert _LIST_BOOKS._request_prefix is _LIST_BOOKS._request_prefix


def test_a_repr_names_the_operation_rather_than_dumping_its_document() -> None:
    """So that a traceback or a debugger shows which operation it is."""
    assert (
        repr(_LIST_BOOKS({}))
        == "Request(operation=Operation(operation_type='query', name='ListBooks'), variables={}, on_execution_error='raise')"
    )


def test_a_request_is_the_spec_s_json_envelope() -> None:
    assert json.loads(bytes(prepare(_LIST_BOOKS({"first": 2})))) == {
        "operationName": "ListBooks",
        "query": "query ListBooks { books { title } }",
        "variables": {"first": 2},
    }
    assert json.loads(bytes(prepare(_LIST_BOOKS({}))))["variables"] == {}


def test_a_response_without_data_is_a_request_error() -> None:
    with pytest.raises(RequestError) as error_info:
        parse_response(
            b'{"errors": [{"message": "Syntax Error"}], "extensions": {"cost": 1}}',
            partial_data_parser=None,
        )

    assert error_info.value.extensions == {"cost": 1}


def test_a_response_with_data_and_errors_is_an_execution_error_holding_the_data() -> (
    None
):
    with pytest.raises(ExecutionError) as error_info:
        parse_response(
            b'{"errors": [{"message": "boom", "path": ["b"]}, {"message": "bang"}], "data": {"a": 1, "b": null}}',
            partial_data_parser=None,
        )

    assert error_info.value.data == {"a": 1, "b": None}
    assert str(error_info.value) == "boom; bang"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        pytest.param(
            b"<html><body>502 Bad Gateway</body></html>",
            "Expected a JSON response",
            id="not json",
        ),
        pytest.param(b"[]", "Expected a JSON object response", id="not an object"),
        pytest.param(
            b'{"extensions": {}}', "Expected the response to carry `data`", id="no data"
        ),
    ],
)
def test_what_is_not_a_graphql_response_is_a_protocol_error(
    raw: bytes, message: str
) -> None:
    with pytest.raises(ProtocolError, match=message):
        parse_response(raw, partial_data_parser=None)


class _GetBook_book(TypedDict):
    title: Annotated[str, NON_NULL]


class _GetBookData(TypedDict):
    book: _GetBook_book | None
    slogan: Annotated[str, NON_NULL]


_GET_BOOK: Final[Operation[Literal["query"], dict[str, object], _GetBookData]] = (
    Operation(
        operation_type="query",
        name="GetBook",
        document="query GetBook {\n  book§: book {\n    title\n  }\n  slogan§: slogan\n}",
        variables_type=dict[str, object],
        data_type=_GetBookData,
    )
)


@pytest.mark.parametrize(
    ("data", "path", "expected"),
    [
        pytest.param(
            {"book": {"title": None}, "slogan": "Read on."},
            ["book", "title"],
            {"book": None, "slogan": "Read on."},
            id="to its nearest nullable parent",
        ),
    ],
)
def test_the_null_of_a_field_asserted_non_null_that_raised_moves_up_in_a_copy(
    data: dict[str, object], path: list[str], expected: dict[str, object] | None
) -> None:
    with pytest.raises(ExecutionError) as error_info:
        parse_data(
            _GET_BOOK,
            json.dumps(
                {"data": data, "errors": [{"message": "boom", "path": path}]}
            ).encode(),
        )

    assert error_info.value.parse_data() == expected
    assert error_info.value.data == data


class _IdempotencyKeyInjector(TypedDict, total=False):
    idempotencyKey: Callable[[], str | OMITTED | None]


def _idempotency_key(function: Callable[[], str | OMITTED | None], /) -> _Injectors:
    return _Injectors(
        {"idempotencyKey": function}, injector_functions_type=_IdempotencyKeyInjector
    )


def _mutation(
    document: str, /, *, injections: dict[str, frozenset[tuple[str, ...]]]
) -> Operation[Literal["mutation"], dict[str, object], dict[str, object]]:
    return Operation(
        operation_type="mutation",
        name="PlaceOrder",
        document=document,
        variables_type=dict[str, object],
        injections=injections,
        data_type=dict[str, object],
    )


def test_an_injector_is_called_once_and_its_value_written_at_every_path() -> None:
    """The caller's variables are left as passed, since the caller may reuse them."""
    operation = _mutation(
        "mutation PlaceOrder($input: PlaceOrderInput!) { placeOrder(input: $input) { id } }",
        injections={"idempotencyKey": frozenset({(), ("input",), ("input", "gift")})},
    )
    calls: list[None] = []

    def idempotency_key() -> str:
        calls.append(None)
        return f"k{len(calls)}"

    variables: dict[str, object] = {"input": {"gift": {"message": "Enjoy!"}}}

    assert resolve_variables(
        operation, variables, injectors=_idempotency_key(idempotency_key)
    ) == {
        "input": {
            "gift": {"message": "Enjoy!", "idempotencyKey": "k1"},
            "idempotencyKey": "k1",
        },
        "idempotencyKey": "k1",
    }
    assert variables == {"input": {"gift": {"message": "Enjoy!"}}}
    assert len(calls) == 1


def test_nothing_is_injected_into_an_input_the_caller_left_out() -> None:
    operation = _mutation(
        "mutation PlaceOrder($input: PlaceOrderInput) { placeOrder(input: $input) { id } }",
        injections={"idempotencyKey": frozenset({("input",)})},
    )

    assert (
        resolve_variables(operation, {}, injectors=_idempotency_key(lambda: "k")) == {}
    )
    assert resolve_variables(
        operation, {"input": None}, injectors=_idempotency_key(lambda: "k")
    ) == {"input": None}


def test_an_injected_value_is_omitted_sent_null_or_left_out_without_its_injector() -> (
    None
):
    operation = _mutation(
        "mutation CancelOrder($idempotencyKey: ID) { cancelOrder(idempotencyKey: $idempotencyKey) { id } }",
        injections={"idempotencyKey": frozenset({()})},
    )

    assert (
        resolve_variables(operation, {}, injectors=_idempotency_key(lambda: OMITTED))
        == {}
    )
    assert resolve_variables(
        operation, {}, injectors=_idempotency_key(lambda: None)
    ) == {"idempotencyKey": None}
    assert (
        resolve_variables(
            operation,
            {},
            injectors=_Injectors({}, injector_functions_type=_IdempotencyKeyInjector),
        )
        == {}
    )

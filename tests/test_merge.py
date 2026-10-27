import json
from itertools import product
from typing import Any

import pytest
from graphql import GraphQLSchema, OperationDefinitionNode, parse, validate

import bookshop.app_graphql
import bookshop.get_order_graphql
from bookshop.graphql.runtime import (
    Operation as BookshopOperation,
    merge as merge_bookshop,
)
from bookshop.injectors import INJECTORS
from graphql_codegen._generator._merge import (
    print_merge_template,
)
from graphql_codegen.runtime import ExecutionError, Operation, Request, merge

_PLACE_ORDER = """
mutation PlaceOrder($order: PlaceOrderInput!, $note: String! = "gift") {
  placed: placeOrder(input: $order, channel: "web") {
    ...OrderSummary
    total
  }
  addNote(note: $note) {
    id
  }
}

fragment OrderSummary on Order {
  id
}
"""

_CANCEL_ORDER = """
mutation CancelOrder($cancellation: CancelOrderInput) {
  cancelOrder(input: $cancellation) {
    __typename
  }
}
"""


def _operation(source: str, /) -> Operation[Any, dict[str, object], object]:
    document = parse(source, no_location=True)
    (definition,) = (
        definition
        for definition in document.definitions
        if isinstance(definition, OperationDefinitionNode)
    )
    assert definition.name is not None
    return Operation(
        operation_type=definition.operation.value,
        name=definition.name.value,
        document=print_merge_template(document),
        variables_type=dict[str, object],
        data_type=object,
    )


def _sent(request: Request[Any, object], /) -> dict[str, Any]:
    return json.loads(bytes(request.prepare()))


def _received[Data](request: Request[Any, Data], payload: object, /) -> Data:
    return request.prepare().parse(json.dumps(payload).encode())


def test_operations_merge_into_one() -> None:
    place_order = _operation(_PLACE_ORDER)
    request = merge(
        place_order({"order": 1, "note": "n"}),
        _operation(_CANCEL_ORDER)({"cancellation": 2}),
        place_order({"order": 3}),
    )

    assert _sent(request) == {
        "operationName": "MergedOperation",
        "query": """mutation MergedOperation($order_0: PlaceOrderInput!, $note_0: String! = "gift", $cancellation_1: CancelOrderInput, $order_2: PlaceOrderInput!, $note_2: String! = "gift") {
  placed_0: placeOrder(input: $order_0, channel: "web") {
    ...OrderSummary
    total
  }
  addNote_0: addNote(note: $note_0) {
    id
  }
  cancelOrder_1: cancelOrder(input: $cancellation_1) {
    __typename
  }
  placed_2: placeOrder(input: $order_2, channel: "web") {
    ...OrderSummary
    total
  }
  addNote_2: addNote(note: $note_2) {
    id
  }
}

fragment OrderSummary on Order {
  id
}""",
        "variables": {"order_0": 1, "note_0": "n", "cancellation_1": 2, "order_2": 3},
    }
    assert _received(
        request, {"data": {"placed_0": 1, "addNote_0": 2, "addNote_2": 3}}
    ) == (
        {"placed": 1, "addNote": 2},
        {},
        {"addNote": 3},
    )


@pytest.mark.parametrize(
    ("source", "name", "query"),
    [
        pytest.param(
            'query Q @cached { a(name: "§") }',
            "Q",
            'query Q @cached {\n  a(name: "\\u00a7")\n}',
            id="or one that cannot be merged, whose `§` in a string is still escaped, so that it is not taken for a sigil",
        ),
        pytest.param(
            _PLACE_ORDER,
            "PlaceOrder",
            'mutation PlaceOrder($order: PlaceOrderInput!, $note: String! = "gift") {\n  placed: placeOrder(input: $order, channel: "web") {\n    ...OrderSummary\n    total\n  }\n  addNote(note: $note) {\n    id\n  }\n}\n\nfragment OrderSummary on Order {\n  id\n}',
            id="keeping an alias of its own",
        ),
        pytest.param(
            _CANCEL_ORDER,
            "CancelOrder",
            "mutation CancelOrder($cancellation: CancelOrderInput) {\n  cancelOrder(input: $cancellation) {\n    __typename\n  }\n}",
            id="without its sigils and the aliases they made redundant, under its own name",
        ),
    ],
)
def test_a_lone_operation_is_sent_without_its_sigils(
    source: str, name: str, query: str
) -> None:
    assert _sent(_operation(source)({"x": 1})) == {
        "operationName": name,
        "query": query,
        "variables": {"x": 1},
    }


@pytest.mark.parametrize(
    ("source", "snippets"),
    [
        pytest.param(
            "query Q($id: ID!) { a { b(id: $id) } }",
            ["b(id: $id_0)", "b(id: $id_1)"],
            id="a variable below the root is suffixed too",
        ),
        pytest.param(
            "query Q { ... on Query { a } b }",
            ["    a_0: a\n", "  b_1: b\n"],
            id="the fields of an inline fragment at the root are aliased",
        ),
        pytest.param(
            'query Q($first: Int) { someRatherLongFieldName(firstArgument: $first, secondArgument: 2, third: "x (y)") { a } }',
            [
                "  someRatherLongFieldName_1: someRatherLongFieldName(\n    firstArgument: $first_1\n",
                '    third: "x (y)"\n  ) {\n    a\n  }',
            ],
            id="arguments the printer breaks over several lines are left in place, parentheses in strings included",
        ),
        pytest.param(
            'query Q($name: String = "$name") { a(name: "§", other: $name) }',
            ['$name_1: String = "$name"', 'a_1: a(name: "\\u00a7", other: $name_1)'],
            id="a sigil or a variable's spelling in a string is left alone",
        ),
    ],
)
def test_an_operation_merged_with_itself(source: str, snippets: list[str]) -> None:
    operation = _operation(source)
    query = _sent(merge(operation({}), operation({})))["query"]

    for snippet in snippets:
        assert snippet in query


def test_an_operation_with_directives_of_its_own_cannot_be_merged() -> None:
    # They would apply to the whole merge, so its template has no sigil, as a subscription's has none.
    operation = _operation("query Q @cached { a }")

    with pytest.raises(ValueError, match="Cannot merge `Q`: its document has no `§`"):
        merge(operation({}), operation({})).prepare()


def test_the_errors_of_a_merge_split_back_to_their_operations_with_their_own_paths() -> (
    None
):
    gift_wrap = _operation("mutation GiftWrap { gift_wrap { id } }")
    first, second = _received(
        merge(
            gift_wrap({}).returning_error(),
            _operation(_CANCEL_ORDER)({}).returning_error(),
        ),
        {
            "data": {"gift_wrap_0": None, "cancelOrder_1": None},
            "errors": [
                {"message": "a", "path": ["cancelOrder_1", "lines", 0]},
                {"message": "b"},
                # A name holding the separator splits at its last one.
                {"message": "c", "path": ["gift_wrap_0"]},
            ],
        },
    )

    assert isinstance(first, ExecutionError)
    assert isinstance(second, ExecutionError)
    assert (first.data, first.errors) == (
        {"gift_wrap": None},
        ({"message": "b"}, {"message": "c", "path": ["gift_wrap"]}),
    )
    assert (second.data, second.errors) == (
        {"cancelOrder": None},
        ({"message": "a", "path": ["cancelOrder", "lines", 0]}, {"message": "b"}),
    )


def test_a_merge_of_one_request_sends_and_reads_it_as_itself() -> None:
    request = merge([_operation(_CANCEL_ORDER)({"cancellation": 2}).returning_error()])
    (error,) = _received(
        request,
        {
            "data": {"cancelOrder": None},
            "errors": [{"message": "boom", "path": ["cancelOrder"]}],
        },
    )

    assert _sent(request)["operationName"] == "CancelOrder"
    assert isinstance(error, ExecutionError)
    assert (error.data, error.errors) == (
        {"cancelOrder": None},
        ({"message": "boom", "path": ["cancelOrder"]},),
    )


@pytest.mark.parametrize(
    ("source", "template"),
    [
        pytest.param(
            "query Q { ...Root } fragment Root on Query { a }",
            "query Q {\n  ... on Query {\n    a§: a\n  }\n}",
            id="a fragment spread at the root is inlined, since its fields need aliases",
        ),
        pytest.param(
            "query Q($x: Int) { a { ...F ...G } } fragment F on A { b(x: $x) } fragment G on A { c }",
            "query Q($x§: Int) {\n  a§: a {\n    ... on A {\n      b(x: $x§)\n    }\n    ...G\n  }\n}\n\nfragment G on A {\n  c\n}",
            id="and so is a fragment using variables, since they need suffixes",
        ),
        pytest.param(
            "query Q { a { ...F ...G } } fragment F on A { ...H } fragment G on A { ...H } fragment H on A { c }",
            "query Q {\n  a§: a {\n    ...F\n    ...G\n  }\n}\n\nfragment F on A {\n  ...H\n}\n\nfragment G on A {\n  ...H\n}\n\nfragment H on A {\n  c\n}",
            id="while any other is left as it is, defined once however many spread it",
        ),
    ],
)
def test_what_cannot_keep_its_shape_in_a_merge_is_inlined_in_the_template(
    source: str, template: str
) -> None:
    assert print_merge_template(parse(source, no_location=True)) == template


def test_every_bookshop_operation_merges_with_every_other_of_its_type_into_a_valid_document(
    bookshop_schema: GraphQLSchema,
) -> None:
    operations = [
        value
        for module in (bookshop.app_graphql, bookshop.get_order_graphql)
        for value in vars(module).values()
        if isinstance(value, BookshopOperation)
    ]
    mergeable = [
        operation
        for operation in operations
        if operation._operation_type != "subscription"
    ]
    assert {operation._operation_type for operation in mergeable} == {
        "query",
        "mutation",
    }

    for operation in operations:
        lone = json.loads(bytes(operation({}).prepare(injectors=INJECTORS)))["query"]
        assert not validate(bookshop_schema, parse(lone)), operation

    for first, second in product(mergeable, repeat=2):
        if first._operation_type != second._operation_type:
            continue

        merged = json.loads(
            bytes(merge_bookshop(first({}), second({})).prepare(injectors=INJECTORS))
        )["query"]
        assert not validate(bookshop_schema, parse(merged)), (first, second)

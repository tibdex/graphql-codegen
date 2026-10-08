from collections.abc import Sequence
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from typing import assert_never, assert_type
from uuid import uuid4

from bookshop.app_graphql import (
    CancelOrder,
    GetBook,
    GetBookData,
    GetBookVariables,
    GetPublication,
    GetPublicationData,
    GetSavedSearch,
    ListBooks,
    OnOrderStatusChanged,
    PlaceOrder,
    Search,
    SearchData,
)
from bookshop.client.injection import injectors
from bookshop.client.runtime import ExecutionError, UnexpectedNullError
from bookshop.client.schema import Address, OrderStatus
from bookshop.get_order_graphql import GetOrder, GetOrderData
from bookshop.scalar import ISBN
from bookshop.transport import Client, SubscriptionClient, transport


def describe(isbn: ISBN, /, *, client: Client) -> str:
    variables: GetBookVariables = {"lookup": {"isbn": isbn}}

    try:
        data = client(GetBook(variables))
    except UnexpectedNullError as error:
        assert error.path == ["book"]
        assert error.__notes__ == [f"Raised by `GetBook` with variables {variables!r}."]
        return "No such book."

    book = data["book"]
    author = book["author"]
    by = "an anthology" if author is None else f"by {author['name']}"
    assert_type(book["price"], Decimal)
    return f"{book['title']}, {by}, costs {book['price']:.2f}."


def book_and_similar(
    isbn: ISBN, text: str, /, *, client: Client
) -> tuple[str, list[str]]:
    # Two queries in one call to the transport.
    book_data, search_data = client(
        (
            GetBook({"lookup": {"isbn": isbn}}),
            Search({"text": text}),
        )
    )
    assert_type(book_data, GetBookData)
    assert_type(search_data, SearchData)
    # Each result is typed by its `__typename`, so only a publication has a `title`.
    titles = [
        result["name"] if result["__typename"] == "Author" else result["title"]
        for result in search_data["search"]
    ]
    return book_data["book"]["title"], titles


def look_up(
    isbns: Sequence[ISBN], publication_ids: Sequence[str], /, *, client: Client
) -> tuple[GetBookData | GetPublicationData, ...]:
    """Fetch the books, then the publications, in one call to the transport."""
    return client(  # ty: ignore[unsound-return-statement]
        [
            *(GetBook({"lookup": {"isbn": isbn}}) for isbn in isbns),
            *(GetPublication({"id": id_}) for id_ in publication_ids),
        ]
    )


def run_saved_search(name: str, /, *, client: Client) -> list[str]:
    data = client(GetSavedSearch({"name": name}))
    search = data["savedSearch"]

    if search is None:
        return []

    # Sent back as is.
    books = client(ListBooks({"filter": search["value"]}))
    return [book["title"] for book in books["books"]]


def length(publication_id: str, /, *, client: Client) -> str:
    data = client(GetPublication({"id": publication_id}))
    publication = data["publication"]

    if publication is None:
        return "No such publication."

    if "pages" in publication:
        return f"{publication['title']} has {publication['pages']} pages."

    match publication["__typename"]:
        case "Audiobook":
            return f"{publication['title']} lasts {publication['duration']} minutes."
        case _ as never:
            assert_never(never)


def status_label(status: OrderStatus, /) -> str:
    match status:
        case "PENDING":
            return "Being prepared"
        case "SHIPPED":
            return "On its way"
        case "DELIVERED":
            return "Delivered"
        case "CANCELED":
            return "Canceled"
        case _:
            # A member added after this client was generated.
            return "Unknown"


def order(book_id: str, address: Address, /, *, client: Client) -> str:
    data = client(
        PlaceOrder(
            {"input": {"lines": [{"book": book_id}], "shippingAddress": address}}
        )
    )
    order = data["placeOrder"]
    assert_type(order["total"], Decimal)
    assert_type(order["placedAt"], datetime)
    return f"Order {order['id']}: {order['total']:.2f} at {order['placedAt']:%H:%M}."


def cancel(order_ids: list[str], /, *, client: Client) -> list[str]:
    """Cancel the orders in one call to the transport, and explain each failure."""
    results = client(
        [
            CancelOrder({"input": {"order": order_id}}).returning_error()
            for order_id in order_ids
        ]
    )
    # Each error has a note naming its operation and the variables sent.
    return [
        f"{error.__notes__[0]} {error!s}"
        for error in results
        if isinstance(error, ExecutionError)
    ]


def track(order_id: str, /, *, client: Client) -> str:
    result = client(GetOrder({"id": order_id}).returning_error())

    if isinstance(result, ExecutionError):
        data = result.parse_data()
        assert_type(data, GetOrderData | None)
        return f"Partially loaded: {data} ({result!s})."

    tracked = result["order"]
    return (
        "No such order."
        if tracked is None
        else f"Order {tracked['id']} is {tracked['status']}."
    )


def watch(order_id: str, /, *, client: SubscriptionClient) -> list[OrderStatus]:
    """Follow the order until it is delivered, and return its statuses."""
    statuses: list[OrderStatus] = []
    events = client(OnOrderStatusChanged({"orderId": order_id}))

    # Closing the stream, however the loop ends, unsubscribes.
    with closing(events):
        for event in events:
            statuses.append(event["orderStatusChanged"]["status"])

            if statuses[-1] == "DELIVERED":
                break

    return statuses


if __name__ == "__main__":
    client = Client(
        transport,
        injectors=injectors({"idempotencyKey": uuid4}),
    )
    print(describe(ISBN("9780141439518"), client=client))

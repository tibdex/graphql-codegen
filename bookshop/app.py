from collections.abc import Sequence
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from typing import Literal, assert_never, assert_type

from bookshop.app_graphql import (
    BookCard,
    CancelOrder,
    GetBook,
    GetBookData,
    GetPages,
    GetPublication,
    GetPublicationData,
    GetSavedSearch,
    ListBooks,
    ListOrders,
    ListTitles,
    ListTitlesVariables,
    OnOrderStatusChanged,
    PlaceOrder,
    PrintedLength,
    Search,
    SearchData,
    TitleCard,
)
from bookshop.get_order_graphql import GetOrder, GetOrderData
from bookshop.graphql.runtime import ExecutionError, OperationRequest, merge
from bookshop.graphql.schema import Address, BookFilter, Genre, OrderStatus
from bookshop.scalar import ISBN
from bookshop.transport import send, subscribe


def titles(
    *genres: Genre, first: int | None = None, with_genres: bool | None = None
) -> list[str]:
    # `$first` is nullable and `$withGenres` has a default, so both keys are `NotRequired`.
    variables: ListTitlesVariables = {
        # A GraphQL list variable takes any `Sequence`, such as this `tuple`.
        "genres": genres,
    }

    # A key left out sends nothing, while `first: None` would send `null`.
    if first is not None:
        variables["first"] = first

    if with_genres is not None:
        variables["withGenres"] = with_genres

    data = send(ListTitles(variables))
    # A GraphQL list field in data is a Python `list`, as JSON decodes it.
    assert_type(data["books"], list[TitleCard])
    labels: list[str] = []

    for book in data["books"]:
        # Selected under `@include`, `genre` is a `NotRequired` key too.
        if "genre" in book:
            assert_type(book["genre"], Genre)
            labels.append(f"{book['title']} ({book['genre'].lower()})")
        else:
            labels.append(book["title"])

    return labels


def card(book: BookCard, /) -> str:
    author = book["author"]
    by = "an anthology" if author is None else f"by {author['name']}"
    return f"{book['title']}, {by}"


def catalog(first: int, /) -> list[str]:
    data = send(ListBooks({"first": first}))
    return [card(book) for book in data["books"]]


def printed_length(printed: PrintedLength, /) -> str:
    assert_type(printed["__typename"], Literal["Book", "Magazine"])
    return f"{printed['title']} has {printed['pages']} pages."


def pages(publication_id: str, /) -> str:
    data = send(GetPages({"id": publication_id}))
    publication = data["publication"]

    if publication is None or "pages" not in publication:
        return "No pages."

    return printed_length(publication)


def describe(isbn: ISBN, /) -> str:
    data = send(GetBook({"lookup": {"isbn": isbn}}))
    book = data["book"]
    # `@nonNull` took `| None` out of `book`'s type, so no check is needed before indexing it.
    author = book["author"]
    # `@nonNull` covers `book` alone, so `author` keeps its `| None`.
    by = "an anthology" if author is None else f"by {author['name']}"

    assert_type(book["price"], Decimal)
    return f"{book['title']}, {by}, costs {book['price']:.2f}."


def book_and_similar(isbn: ISBN, text: str, /) -> tuple[str, list[str]]:
    # Two queries in a single transport request.
    book_data, search_data = send(
        merge(
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
    isbns: Sequence[ISBN], publication_ids: Sequence[str], /
) -> tuple[GetBookData | GetPublicationData, ...]:
    # Any number of queries in a single transport request.
    queries: list[
        OperationRequest[Literal["query"], GetBookData | GetPublicationData]
    ] = [
        *(GetBook({"lookup": {"isbn": isbn}}) for isbn in isbns),
        *(GetPublication({"id": id_}) for id_ in publication_ids),
    ]
    return send(merge(queries))


def run_saved_search(name: str, /) -> list[str]:
    data = send(GetSavedSearch({"name": name}))
    search = data["savedSearch"]

    if search is None:
        return []

    book_filter = search["value"]
    assert_type(book_filter, BookFilter | None)
    # Sent back as is.
    books = send(ListBooks({"filter": book_filter}))
    return [book["title"] for book in books["books"]]


def length(publication_id: str, /) -> str:
    data = send(GetPublication({"id": publication_id}))
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
            # A member added after codegen.
            return status.replace("_", " ").capitalize()


def cheaper_than(limit: Decimal, /) -> list[str]:
    data = send(ListBooks({"filter": {"priceBelow": limit}}))
    labels: list[str] = []

    for book in data["books"]:
        price = book["price"]
        assert_type(price, Decimal)
        labels.append(f"{card(book)}: {price:.2f}")

    return labels


def order(book_id: str, address: Address, /) -> str:
    data = send(
        PlaceOrder(
            {"input": {"lines": [{"book": book_id}], "shippingAddress": address}}
        )
    )
    order = data["placeOrder"]
    assert_type(order["total"], Decimal)
    assert_type(order["placedAt"], datetime)
    return f"Order {order['id']}: {order['total']:.2f} at {order['placedAt']:%H:%M}."


def order_ids() -> list[str]:
    data = send(ListOrders({}))
    return [order["id"] for order in data["orders"]]


def track(order_id: str, /) -> str:
    result = send(GetOrder({"id": order_id}).returning_error())

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


def cancel(order_ids: list[str], /) -> list[str]:
    results = send(
        merge(
            [
                CancelOrder({"input": {"order": order_id}}).returning_error()
                for order_id in order_ids
            ]
        )
    )
    # Each error has a note naming its operation and the variables sent.
    return [
        f"{error.__notes__[0]} {error!s}"
        for error in results
        if isinstance(error, ExecutionError)
    ]


def watch(order_id: str, /) -> list[OrderStatus]:
    statuses: list[OrderStatus] = []
    events = subscribe(OnOrderStatusChanged({"orderId": order_id}))

    with closing(events):
        for event in events:
            statuses.append(event["orderStatusChanged"]["status"])

            if statuses[-1] == "DELIVERED":
                break

    return statuses

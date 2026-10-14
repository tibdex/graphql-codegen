from contextlib import aclosing
from typing import assert_type

from bookshop.app_graphql import GetBook, GetBookData, OnOrderStatusChanged
from bookshop.async_transport import send, subscribe
from bookshop.graphql.schema import OrderStatus
from bookshop.scalar import ISBN


async def title(isbn: ISBN, /) -> str:
    data = await send(GetBook({"lookup": {"isbn": isbn}}), timeout=5.0)
    assert_type(data, GetBookData)
    return data["book"]["title"]


async def watch(order_id: str, /) -> list[OrderStatus]:
    statuses: list[OrderStatus] = []
    events = subscribe(OnOrderStatusChanged({"orderId": order_id}))

    async with aclosing(events):
        async for event in events:
            statuses.append(event["orderStatusChanged"]["status"])

            if statuses[-1] == "DELIVERED":
                break

    return statuses

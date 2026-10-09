from typing import Literal, assert_never, assert_type

from bookshop.client.runtime import Client
from bookshop.client.schema import OrderStatus
from bookshop.get_order_graphql import GetOrder
from bookshop.transport import transport

client = Client(transport)
data = client(GetOrder({"id": "o1"}))
order = data["order"]

# `Query.order`'s type is nullable, so the type checker requires this test.
if order is None:
    print("No such order.")
else:
    # `Order.id: ID!` is a `str` on the wire.
    assert_type(order["id"], str)

    # An `enum` gets a generated alias of the `Literal` of its values.
    assert_type(order["status"], OrderStatus)
    assert_type(order["status"], Literal["PENDING", "SHIPPED", "DELIVERED", "CANCELED"])

    if "total" in order:
        # A field not selected in the GraphQL operation can never be there.
        assert_never(order)

    print(f"Order {order['id']} is {order['status']}.")

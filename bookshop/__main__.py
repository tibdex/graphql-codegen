from uuid import uuid4

from bookshop.app import describe
from bookshop.client.injection import injectors
from bookshop.scalar import ISBN
from bookshop.transport import Client, transport

client = Client(
    transport,
    injectors=injectors({"idempotencyKey": uuid4}),
)
print(describe(ISBN("9780141439518"), client=client))

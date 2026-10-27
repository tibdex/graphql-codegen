from contextvars import ContextVar
from typing import Final
from uuid import uuid4

from bookshop.graphql.injection import injectors
from bookshop.graphql.runtime import OMITTED

CURRENT_CUSTOMER: Final = ContextVar[str | None]("CURRENT_CUSTOMER", default=None)


def customer() -> str | OMITTED:
    current = CURRENT_CUSTOMER.get()
    if current is None:
        # The app authenticates with its own account and acts for the customer it serves.
        # With nobody to serve, the field is left out rather than null, so the server acts for that account.
        return OMITTED
    return current


INJECTORS: Final = injectors(
    {
        "customer": customer,
        "idempotencyKey": uuid4,
    }
)

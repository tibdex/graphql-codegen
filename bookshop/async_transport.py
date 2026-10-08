from collections.abc import AsyncGenerator
from typing import Protocol, TypeAlias

import httpx2

from bookshop import mime_type
from bookshop.client.runtime import (
    AsyncClient as _AsyncClient,
    AsyncSubscriptionClient as _AsyncSubscriptionClient,
)

http = httpx2.AsyncClient(base_url="https://bookshop.example")
HEADERS = {"Accept": mime_type.GRAPHQL_RESPONSE, "Content-Type": mime_type.JSON}


async def transport(body: bytes, /, *, timeout: float | None = None) -> bytes:
    response = await http.post(
        "/graphql", content=body, headers=HEADERS, timeout=timeout
    )

    # GraphQL over HTTP sends a request error as a response with a 4xx status.
    if not response.headers.get("Content-Type", "").startswith(
        mime_type.GRAPHQL_RESPONSE
    ):
        response.raise_for_status()

    return response.content


class Transport(Protocol):
    async def __call__(
        self, body: bytes, /, *, timeout: float | None = None
    ) -> bytes: ...


# Keeps the protocol in sync.
_transport: Transport = transport

AsyncClient: TypeAlias = _AsyncClient[Transport]  # noqa: UP040


async def subscription_transport(body: bytes, /) -> AsyncGenerator[bytes, None]:
    """Yield the data of each `next` event, following the GraphQL over Server-Sent Events protocol."""
    # Closing the generator leaves the block, which closes the connection: that is how to unsubscribe.
    async with http.sse(
        "/graphql",
        method="POST",
        content=body,
        headers={"Content-Type": mime_type.JSON},
    ) as events:
        async for event in events:
            if event.event == "complete":
                return

            if event.event == "next":
                yield event.data.encode()


class SubscriptionTransport(Protocol):
    def __call__(self, body: bytes, /) -> AsyncGenerator[bytes, None]: ...


# Keeps the protocol in sync.
_subscription_transport: SubscriptionTransport = subscription_transport

AsyncSubscriptionClient: TypeAlias = _AsyncSubscriptionClient[SubscriptionTransport]  # noqa: UP040

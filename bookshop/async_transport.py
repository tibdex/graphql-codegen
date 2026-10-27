"""The bookshop's asynchronous IO, with httpx2."""

from collections.abc import AsyncGenerator
from typing import Final, Literal

import httpx2

from bookshop.graphql.runtime import Request

GRAPHQL_RESPONSE: Final = "application/graphql-response+json"
http: Final = httpx2.AsyncClient(
    base_url="https://bookshop.example",
    headers={
        "Accept": GRAPHQL_RESPONSE,
        "Content-Type": "application/json",
    },
)


async def send[Data](
    request: Request[Literal["query", "mutation"], Data],
    /,
    *,
    timeout: float | None = None,
) -> Data:
    prepared = request.prepare()
    response = await http.post("/graphql", content=bytes(prepared), timeout=timeout)

    # GraphQL over HTTP sends a request error as a response with a 4xx or 5xx status.
    if not response.headers.get("Content-Type", "").startswith(GRAPHQL_RESPONSE):
        response.raise_for_status()

    return prepared.parse(response.content)


async def subscribe[Data](
    request: Request[Literal["subscription"], Data], /
) -> AsyncGenerator[Data, None]:
    prepared = request.prepare()

    # Closing the generator leaves the block, closes the connection, and ends the subscription.
    async with http.sse("/graphql", method="POST", content=bytes(prepared)) as events:
        async for event in events:
            if event.event == "complete":
                return

            if event.event == "next":
                yield prepared.parse(event.data.encode())

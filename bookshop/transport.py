"""The bookshop's synchronous transports (using the standard library alone)."""

from collections.abc import Generator
from http.client import HTTPResponse
from typing import Protocol, TypeAlias
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bookshop import mime_type
from bookshop.client.runtime import (
    Client as _Client,
    SubscriptionClient as _SubscriptionClient,
)

BASE_URL = "https://bookshop.example"


def _post(
    path: str, body: bytes, /, *, accept: str, timeout: float | None
) -> HTTPResponse:
    request = Request(  # noqa: S310
        BASE_URL + path,
        data=body,
        headers={"Accept": accept, "Content-Type": mime_type.JSON},
    )

    response = urlopen(request, timeout=timeout)  # noqa: S310
    # The only response an `http:` or `https:` URL gives.
    assert isinstance(response, HTTPResponse)
    return response


def transport(body: bytes, /, *, timeout: float | None = None) -> bytes:
    retries = 2

    while True:
        try:
            response = _post(
                "/graphql", body, accept=mime_type.GRAPHQL_RESPONSE, timeout=timeout
            )
        except HTTPError as error:
            # GraphQL over HTTP sends a request error as a response with a 4xx status.
            if error.headers.get_content_type() != mime_type.GRAPHQL_RESPONSE:
                raise

            response = error
        except ConnectionError:
            # The response was lost.
            if not retries:
                raise

            retries -= 1
            continue

        with response:
            return response.read()


class Transport(Protocol):
    def __call__(self, body: bytes, /, *, timeout: float | None = None) -> bytes: ...


# Keeps the protocol in sync.
_transport: Transport = transport


# Not a `type` statement, which could not be called to build a client.
Client: TypeAlias = _Client[Transport]  # noqa: UP040


def subscription_transport(body: bytes, /) -> Generator[bytes, None, None]:
    """Yield the data of each `next` event, following the GraphQL over Server-Sent Events protocol."""
    # Closing the generator leaves the block, which closes the connection: that is how to unsubscribe.
    with _post(
        "/graphql", body, accept=mime_type.EVENT_STREAM, timeout=None
    ) as response:
        event = ""
        data: list[str] = []

        for raw_line in response:
            line = raw_line.decode().rstrip("\r\n")

            # A blank line ends the event.
            if not line:
                if event == "complete":
                    return

                if event == "next":
                    yield "\n".join(data).encode()

                event = ""
                data = []
                continue

            field, _, value = line.partition(":")
            value = value.removeprefix(" ")

            if field == "event":
                event = value
            elif field == "data":
                data.append(value)


class SubscriptionTransport(Protocol):
    def __call__(self, body: bytes, /) -> Generator[bytes, None, None]: ...


# Keeps the protocol in sync.
_subscription_transport: SubscriptionTransport = subscription_transport

SubscriptionClient: TypeAlias = _SubscriptionClient[SubscriptionTransport]  # noqa: UP040

"""The bookshop's synchronous IO, with the standard library alone."""

from collections.abc import Generator
from http.client import HTTPResponse
from typing import Final, Literal
from urllib.error import HTTPError
from urllib.request import Request as HTTPRequest, urlopen

from bookshop.graphql.runtime import Request
from bookshop.injectors import INJECTORS

BASE_URL: Final = "https://bookshop.example"
ATTEMPTS: Final = 3


def _post(
    path: str, body: bytes, /, *, accept: str, timeout: float | None
) -> HTTPResponse:
    request = HTTPRequest(  # noqa: S310
        BASE_URL + path,
        headers={"Accept": accept, "Content-Type": "application/json"},
        data=body,
    )

    response = urlopen(request, timeout=timeout)  # noqa: S310
    # The only response an `http:` or `https:` URL gives.
    assert isinstance(response, HTTPResponse)
    return response


def send[Data](
    request: Request[Literal["query", "mutation"], Data],
    /,
    *,
    timeout: float | None = None,
) -> Data:
    prepared = request.prepare(injectors=INJECTORS)
    # Built once, so that a retry sends the same idempotency key.
    request_body = bytes(prepared)
    attempts_left = ATTEMPTS

    # A connection per call keeps the example simple: an HTTP library, such as httpx2 in `async_transport.py`, would pool them.
    while True:
        # Declared, since mypy types a variable from its first assignment alone.
        response: HTTPResponse | HTTPError

        try:
            response = _post(
                "/graphql",
                request_body,
                # Falls back to the plain JSON media type for a server predating the GraphQL one.
                accept="application/graphql-response+json, application/json;q=0.9",
                timeout=timeout,
            )
        except HTTPError as error:
            # GraphQL over HTTP sends a request error as a response with a 4xx or 5xx status.
            if error.headers.get_content_type() != "application/graphql-response+json":
                raise

            response = error
        except ConnectionError:
            attempts_left -= 1

            if not attempts_left:
                raise

            continue

        with response:
            response_body = response.read()

        return prepared.parse(response_body)


def subscribe[Data](
    request: Request[Literal["subscription"], Data], /
) -> Generator[Data, None, None]:
    prepared = request.prepare(injectors=INJECTORS)
    request_body = bytes(prepared)

    # Closing the generator leaves the block, closes the connection, and ends the subscription.
    with _post(
        "/graphql", request_body, accept="text/event-stream", timeout=None
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
                    yield prepared.parse("\n".join(data).encode())

                event = ""
                data = []
                continue

            field, _, value = line.partition(":")
            value = value.removeprefix(" ")

            if field == "event":
                event = value
            elif field == "data":
                data.append(value)

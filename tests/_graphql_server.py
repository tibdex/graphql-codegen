import json
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler
from typing import Protocol

from tests._server import PostHandler


class GraphqlHandler(Protocol):
    """What a local GraphQL server answers for each request."""

    def __call__(self, query: str, /, *, headers: Mapping[str, str]) -> object: ...


def answer_graphql(graphql_handler: GraphqlHandler, /) -> PostHandler:
    def handle(handler: BaseHTTPRequestHandler, /) -> None:
        request_body = json.loads(
            handler.rfile.read(int(handler.headers["Content-Length"]))
        )
        response_body = graphql_handler(
            request_body["query"], headers=dict(handler.headers)
        )
        payload = json.dumps(response_body)
        handler.send_response(200)
        handler.end_headers()
        handler.wfile.write(payload.encode())

    return handle

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import override

type PostHandler = Callable[[BaseHTTPRequestHandler], None]
"""How a local HTTP server handles each POST request."""


@contextmanager
def serve(post_handler: PostHandler, /) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            post_handler(self)

        @override
        def log_message(self, format: str, *args: object) -> None:
            """Keep the test's output clean."""

    host = "127.0.0.1"

    with ThreadingHTTPServer((host, 0), Handler) as server:
        server.daemon_threads = True
        # Polled often, so that shutting down is quick.
        Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()

        try:
            yield f"http://{host}:{server.server_port}"
        finally:
            server.shutdown()

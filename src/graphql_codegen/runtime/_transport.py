# Copied into each generated package, where this line says not to edit it.

from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from typing import Concatenate

type Transport[**Params] = Callable[Concatenate[bytes, Params], bytes]
"""The spec leaves the transport mechanism to the implementation, and so does this library.
Bytes in, bytes out keeps it sans-IO: it owns neither the transport nor the connection, so it works under any IO model.

"""

type AsyncTransport[**Params] = Callable[Concatenate[bytes, Params], Awaitable[bytes]]

type SubscriptionTransport[**Params] = Callable[
    Concatenate[bytes, Params], Generator[bytes, None, None]
]
"""A generator rather than any iterator, because closing the response stream closes it: its cleanup is where it unsubscribes."""

type AsyncSubscriptionTransport[**Params] = Callable[
    Concatenate[bytes, Params], AsyncGenerator[bytes, None]
]

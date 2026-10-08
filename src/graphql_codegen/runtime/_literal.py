# Copied into each generated package, where this line says not to edit it.

from typing import Literal

type OperationType = Literal["query", "mutation", "subscription"]
"""The spec's `OperationType`.

- ``"query"`` reads, and a client may run several at once.
- ``"mutation"`` writes, and the server runs the root fields of several in order.
- ``"subscription"`` answers with a stream of responses rather than one, which only a subscription client runs.
"""

type ExecutionErrorHandling = Literal["raise", "return"]
"""What a client does with the :class:`~.error.ExecutionError` of the response to a request.

- ``"raise"`` raises it, so that what a call returns is always complete data.
- ``"return"`` returns it in place of the request's data, typed by the request, for a caller who can use partial data.
"""

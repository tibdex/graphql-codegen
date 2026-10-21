# Copied into each generated package, where this line becomes a `DO NOT EDIT` header.

from typing import Literal

type ExecutionErrorHandling = Literal["raise", "return"]
"""What `PreparedRequest.parse()` does with the `ExecutionError` of a response.

- `"raise"` raises it, so the data returned is always complete.
- `"return"` returns it, so the data returned may be the error, typed by the request.
"""

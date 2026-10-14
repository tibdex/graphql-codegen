# Copied into each generated package, where this line says not to edit it.

from typing import Final, Literal

type OperationType = Literal["query", "mutation", "subscription"]

# Generated operations pass these rather than a string so they do not have to repeat their type in an assignment annotation.
# Pyrefly promotes an inferred literal to `str` in a comprehension's element type: a bare `Final` (which ruff wants) infers it, while `Final[Literal[...]]` declares it.
QUERY: Final[Literal["query"]] = "query"  # noqa: PYI064
MUTATION: Final[Literal["mutation"]] = "mutation"  # noqa: PYI064
SUBSCRIPTION: Final[Literal["subscription"]] = "subscription"  # noqa: PYI064

# Copied into each generated package, where this line becomes a `DO NOT EDIT` header.

import sys
from collections.abc import Callable, Mapping
from typing import Final, cast, final

from ._reflection import build_injector_serializers

if sys.version_info >= (3, 15):
    OMITTED = sentinel("OMITTED")  # noqa: F821
else:
    from typing_extensions import Sentinel

    OMITTED = Sentinel("OMITTED")
"""What an injector returns to leave its value out, rather than send `null`.

A nullable input accepts both, and a server may treat them differently.
"""


type _Injector = Callable[[], object | OMITTED]


@final
class _Injectors:
    """Only the generated `injectors()` builds one, so `Request.prepare()` never calls a function the package's types did not check."""

    def __init__(
        self, functions: Mapping[str, object], /, *, injector_functions_type: object
    ) -> None:
        self._functions: Final = {
            name: cast(_Injector, function) for name, function in functions.items()
        }
        self._serializers: Final = build_injector_serializers(injector_functions_type)

    def _inject(
        self,
        variables: Mapping[str, object],
        /,
        *,
        injections: Mapping[str, frozenset[tuple[str, ...]]],
    ) -> Mapping[str, object]:
        for name, function in self._functions.items():
            if name not in injections:
                continue

            # Called once per request, so every path gets the same value.
            value = function()

            if value is OMITTED:
                continue

            serialize = self._serializers[name]
            serialized = value if serialize is None else serialize(value)

            for path in injections[name]:
                injected = _inject_into(variables, path, key=name, value=serialized)

                if injected is not None:
                    variables = injected

        return variables


def _inject_into(
    container: Mapping[str, object],
    path: tuple[str, ...],
    /,
    *,
    key: str,
    value: object,
) -> dict[str, object] | None:
    if not path:
        return {**container, key: value}

    head, *rest = path
    child = container.get(head)

    if child is None:
        return None

    assert isinstance(child, Mapping), (
        f"Expected `{head}` to hold an input object to inject `{key}` into."
    )
    injected = _inject_into(child, tuple(rest), key=key, value=value)
    return None if injected is None else {**container, head: injected}

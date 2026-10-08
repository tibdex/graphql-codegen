# Copied into each generated package, where this line says not to edit it.

import sys
from collections.abc import Callable, Mapping
from typing import Final, cast, final

from ._reflection import Convert, build_injector_serializers

if sys.version_info >= (3, 15):
    OMITTED = sentinel("OMITTED")  # noqa: F821
else:
    from typing_extensions import Sentinel

    OMITTED = Sentinel("OMITTED")
"""What an injector returns to leave its value out rather than send ``null``.

The spec tells the two apart: a nullable input accepts both, a non-null one neither, and a server may treat them differently.
"""


type _Injector = Callable[[], object | OMITTED]


@final
class _Injectors:
    """Built by the ``injectors()`` function of a generated package's ``injection`` module, from a mapping the package's types check.

    Not a mapping itself, so that a client cannot be given injectors that skipped that check: only a client calls it.
    """

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
        """Calling the injectors is the one impure step, each once, so that every path of a request receives the same value."""
        values = {
            name: function()
            for name, function in self._functions.items()
            if name in injections
        }
        return _inject_values(
            variables, values, serializers=self._serializers, injections=injections
        )


def _inject_values(
    variables: Mapping[str, object],
    values: Mapping[str, object],
    /,
    *,
    serializers: Mapping[str, Convert | None],
    injections: Mapping[str, frozenset[tuple[str, ...]]],
) -> Mapping[str, object]:
    for name, value in values.items():
        if value is OMITTED:
            continue

        serialize = serializers[name]
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
    """Copied along the path, since the caller may reuse what it passed.

    ``None`` when an object on the path is absent or null, as a nullable input the caller left out is: there is nothing to inject into.
    """
    if not path:
        return {**container, key: value}

    head, *rest = path
    child = container.get(head)

    if child is None:
        return None

    # The generator only emits paths through input objects.
    assert isinstance(child, Mapping), (
        f"Expected `{head}` to hold an input object to inject `{key}` into."
    )
    injected = _inject_into(child, tuple(rest), key=key, value=value)
    return None if injected is None else {**container, head: injected}

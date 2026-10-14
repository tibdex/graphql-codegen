from dataclasses import dataclass
from functools import cached_property
from typing import final

from graphql_codegen._error_note import error_note
from graphql_codegen._generator.dotted_name import Reference, parse_dotted_name


@final
@dataclass(frozen=True, kw_only=True)
class Codec:
    """The functions converting a scalar's values between JSON and Python, such as a `datetime` sent as an ISO 8601 string."""

    decode: str
    """The dotted name of the function turning the JSON value into the Python one."""

    encode: str
    """The dotted name of the function turning the Python value into the JSON one."""

    def __post_init__(self) -> None:
        # Fail on a bad name now, not at first use.
        _ = self._decode_reference, self._encode_reference

    @cached_property
    def _decode_reference(self) -> Reference:
        with error_note("In `decode`."):
            return parse_dotted_name(self.decode)

    @cached_property
    def _encode_reference(self) -> Reference:
        with error_note("In `encode`."):
            return parse_dotted_name(self.encode)


@final
@dataclass(frozen=True, kw_only=True)
class Scalar:
    """How a scalar is typed, and converted when its Python value differs from its JSON one.

    A built-in scalar can be given a type too, such as `ID` typed `my_app.UserId`.
    """

    type: str
    """The dotted name of its Python type.

    Without a codec, it must be the type JSON decodes to: a builtin such as `int`, or a `NewType` or alias of one.
    """

    codec: Codec | None = None
    """When `None`, the value stays as JSON decodes it."""

    def __post_init__(self) -> None:
        # Fail on a bad name now, not at first use.
        _ = self._type_reference

    @cached_property
    def _type_reference(self) -> Reference:
        with error_note("In `type`."):
            return parse_dotted_name(self.type)

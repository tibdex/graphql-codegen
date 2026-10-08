from dataclasses import dataclass
from functools import cached_property
from typing import final

from graphql_codegen._generator.dotted_name import Reference, parse_dotted_name
from graphql_codegen._note import error_note


@final
@dataclass(frozen=True, kw_only=True)
class Codec:
    """The functions converting a scalar's value between JSON and Python, such as a :class:`~datetime.datetime` sent as an ISO string."""

    decode: str
    """The dotted name of the function converting its JSON value to its Python one."""

    encode: str
    """The dotted name of the function converting its Python value to its JSON one."""

    def __post_init__(self) -> None:
        # Parsed eagerly, so that no codec holds a dotted name it cannot use.
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
    """How a scalar is typed, and converted if its Python value differs from its JSON one.

    Not only a custom scalar: a built-in one can be given a type of its own too, such as `ID` typed `my_app.UserId`.
    """

    type: str
    """The dotted name of its Python type.

    When :attr:`codec` is ``None``, it must describe the decoded JSON value: a builtin, such as :class:`int`, or a :class:`typing.NewType` or type alias of one, such as `my_app.Isbn = NewType("Isbn", str)`.
    """

    codec: Codec | None = None
    """When ``None``, the value stays as JSON decodes it."""

    def __post_init__(self) -> None:
        # Parsed eagerly, so that no scalar holds a dotted name it cannot use.
        _ = self._type_reference

    @cached_property
    def _type_reference(self) -> Reference:
        with error_note("In `type`."):
            return parse_dotted_name(self.type)

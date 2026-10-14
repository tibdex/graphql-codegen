# Copied into each generated package, where this line says not to edit it.

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from threading import Lock
from types import NoneType, UnionType
from typing import (
    Annotated,
    Final,
    Literal,
    NotRequired,
    Required,
    TypeAliasType,
    Union,
    assert_never,
    cast,
    final,
    get_args,
    get_origin,
)

from ._compat import (
    ReadOnly,
    SentinelType,
    get_type_hints,
    is_typeddict,
)
from .error import UnexpectedNullError

type Mode = Literal["parse", "partial parse", "serialize"]
"""What a builder's converters do.

- `"parse"` converts the data of a response in place, and raises on the null of a `@nonNull` field.
- `"partial parse"` does the same for a response with errors, but moves the null of a `@nonNull` field up to its nearest nullable parent, as the server does for a non-null field.
- `"serialize"` converts a copy of the variables to what goes on the wire.
"""

type Convert = Callable[[object], object]
"""A parser converts in place, a serializer copies: a response is ours, the variables are the caller's."""

_QUALIFIERS: Final = frozenset({NotRequired, ReadOnly, Required})


NON_NULL: Final = SentinelType("NON_NULL")
"""Marks a `@nonNull` field, whose null raises `UnexpectedNullError`."""


@final
@dataclass(frozen=True, kw_only=True)
class Codec[Value, Wire]:
    decode: Callable[[Wire], Value]
    encode: Callable[[Value], Wire]


def _unwrap(type_: object, /) -> tuple[object, tuple[object, ...]]:
    metadata: list[object] = []

    while True:
        if isinstance(type_, TypeAliasType):
            type_ = type_.__value__
        elif get_origin(type_) in _QUALIFIERS:
            (type_,) = get_args(type_)
        elif get_origin(type_) is Annotated:
            type_, *extra = get_args(type_)
            metadata.extend(extra)
        else:
            return type_, tuple(metadata)


def _union_members(type_: object, /) -> tuple[object, ...] | None:
    return get_args(type_) if get_origin(type_) in (Union, UnionType) else None


def _typename_values(type_: object, /) -> tuple[object, ...] | None:
    hints = get_type_hints(type_, include_extras=True)
    typename = hints.get("__typename")

    if typename is None:
        return None

    literal, _ = _unwrap(typename)
    assert get_origin(literal) is Literal, (
        f"Expected `{type_}.__typename` to be a `Literal`."
    )
    return get_args(literal)


def _children(unwrapped_type: object, /) -> tuple[object, ...]:
    if (members := _union_members(unwrapped_type)) is not None:
        return members

    if get_origin(unwrapped_type) in (list, Sequence):
        return get_args(unwrapped_type)

    if is_typeddict(unwrapped_type):
        return tuple(get_type_hints(unwrapped_type, include_extras=True).values())

    return ()


@final
class _Builder:
    """Builds the converters of one mode, once per type.

    It recurses over the grammar of types, not over the shape of any document, so nesting, lists of lists, and recursive inputs need no special case.
    """

    def __init__(self, mode: Mode, /) -> None:
        match mode:
            case "parse":
                parses, partial = True, False
            case "partial parse":
                parses, partial = True, True
            case "serialize":
                parses, partial = False, False
            case _ as never:
                assert_never(never)

        self._parses: Final = parses
        self._partial: Final = partial
        self._needs: Final[dict[object, bool]] = {}
        self._converters: Final[dict[object, Convert]] = {}
        # A recursive type caches a forwarding converter before its real one exists, which another thread must not call.
        self._lock: Final = Lock()

    def _needs_work(self, type_: object, /) -> bool:
        if type_ not in self._needs:
            self._solve_needs(type_)

        return self._needs[type_]

    def _solve_needs(self, root: object, /) -> None:
        # A least fixed point, since types reach each other in cycles, such as a filter combining filters.
        children: dict[object, tuple[object, ...]] = {}
        needs: dict[object, bool] = {}
        pending = [root]

        while pending:
            type_ = pending.pop()

            if type_ in children or type_ in self._needs:
                continue

            inner, metadata = _unwrap(type_)
            needs[type_] = any(isinstance(item, Codec) for item in metadata) or (
                NON_NULL in metadata and self._parses
            )
            children[type_] = _children(inner)
            pending.extend(children[type_])

        changed = True

        while changed:
            changed = False

            for type_, type_children in children.items():
                if not needs[type_] and any(
                    needs[child] if child in needs else self._needs[child]
                    for child in type_children
                ):
                    needs[type_] = True
                    changed = True

        self._needs.update(needs)

    def build(self, type_: object, /) -> Convert | None:
        """Return the converter of *type_*, or `None` if its values need no work."""
        with self._lock:
            return self._build(type_)

    def _build(self, type_: object, /) -> Convert | None:
        return self._converter(type_) if self._needs_work(type_) else None

    def _converter(self, type_: object, /) -> Convert:
        if type_ in self._converters:
            return self._converters[type_]

        # A recursive type reaches itself while being built, and gets a forwarding converter until then.
        cell: list[Convert] = []
        self._converters[type_] = lambda value: cell[0](value)  # noqa: PLW0108
        converter = self._new_converter(type_)
        cell.append(converter)
        self._converters[type_] = converter
        return converter

    def _new_converter(self, type_: object, /) -> Convert:
        inner, metadata = _unwrap(type_)
        codecs = [item for item in metadata if isinstance(item, Codec)]
        convert: Convert | None = None

        if codecs:
            (codec,) = codecs
            convert = cast(Convert, codec.decode if self._parses else codec.encode)
        elif self._needs_work(inner):
            convert = self._build_structure(inner)

        if NON_NULL in metadata and self._parses:
            return _non_null_converter(convert)

        assert convert is not None, f"Expected `{type_}` to need work."
        return convert

    def _build_structure(self, type_: object, /) -> Convert:
        if (members := _union_members(type_)) is not None:
            return self._build_union(type_, members)

        if get_origin(type_) in (list, Sequence):
            (item,) = get_args(type_)
            return _list_converter(self._converter(item))

        assert is_typeddict(type_), f"Expected `{type_}` to need no work."
        return self._build_typed_dict(type_)

    def _build_typed_dict(self, type_: object, /) -> Convert:
        steps = tuple(
            (key, converter)
            for key, hint in get_type_hints(type_, include_extras=True).items()
            if (converter := self._build(hint)) is not None
        )
        in_place = self._parses

        def convert(value: object, /) -> object:
            assert isinstance(value, Mapping)
            result = cast(dict[str, object], value) if in_place else dict(value)

            for key, convert_value in steps:
                # Absent for an optional input, or a field the server skipped.
                if key in result:
                    try:
                        result[key] = convert_value(result[key])
                    except UnexpectedNullError as error:
                        error.path.insert(0, key)
                        raise

            return result

        return convert

    def _build_union(self, type_: object, members: tuple[object, ...], /) -> Convert:
        # A sentinel, such as `OMITTED`, is not converted, like `None`.
        non_null = tuple(
            member
            for member in members
            if member is not NoneType and not isinstance(member, SentinelType)
        )
        nullable = NoneType in members

        if len(non_null) == 1:
            convert = self._converter(non_null[0])
        else:
            convert = self._build_dispatch(type_, non_null)

        if not nullable:
            return convert

        partial = self._partial

        def convert_nullable(value: object, /) -> object:
            try:
                return None if value is None else convert(value)
            except UnexpectedNullError:
                if partial:
                    return None

                raise

        return convert_nullable

    def _build_dispatch(self, type_: object, members: tuple[object, ...], /) -> Convert:
        typed_dicts = [_unwrap(member)[0] for member in members]

        if not all(is_typeddict(member) for member in typed_dicts):
            raise TypeError(
                f"Expected the members of `{type_}` needing work to be TypedDicts."
            )

        converters = [self._build(member) for member in members]
        typenames = [_typename_values(member) for member in typed_dicts]

        if all(values is not None for values in typenames):
            by_typename = {
                value: converter
                for values, converter in zip(typenames, converters, strict=True)
                for value in values or ()
            }

            def convert_by_typename(value: object, /) -> object:
                assert isinstance(value, Mapping)
                # A member added after codegen passes as is: nothing is validated.
                converter = by_typename.get(value["__typename"])
                return value if converter is None else converter(value)

            return convert_by_typename

        keys = [tuple(get_type_hints(member)) for member in typed_dicts]

        if not all(len(member_keys) == 1 for member_keys in keys) or len(
            set(keys)
        ) != len(keys):
            raise TypeError(f"Cannot tell the members of `{type_}` apart.")

        by_key = {
            member_key: converter
            for (member_key,), converter in zip(keys, converters, strict=True)
        }

        def convert_by_key(value: object, /) -> object:
            assert isinstance(value, Mapping)
            (key,) = value
            # A member added after codegen passes as is, like above.
            converter = by_key.get(key)
            return value if converter is None else converter(value)

        return convert_by_key


def _list_converter(convert_item: Convert, /) -> Convert:
    def convert(value: object, /) -> object:
        assert isinstance(value, Sequence)
        result: list[object] = []

        for index, item in enumerate(value):
            try:
                result.append(convert_item(item))
            except UnexpectedNullError as error:
                error.path.insert(0, index)
                raise

        return result

    return convert


def _non_null_converter(convert: Convert | None, /) -> Convert:
    def check(value: object, /) -> object:
        if value is None:
            raise UnexpectedNullError([])

        return value if convert is None else convert(value)

    return check


_PARSE: Final = _Builder("parse")
_PARTIAL_PARSE: Final = _Builder("partial parse")
_SERIALIZE: Final = _Builder("serialize")


def build_parser(type_: object, /, *, partial: bool) -> Convert | None:
    return (_PARTIAL_PARSE if partial else _PARSE).build(type_)


def build_serializer(type_: object, /) -> Convert | None:
    return _SERIALIZE.build(type_)


def build_injector_serializers(
    injector_functions_type: object, /
) -> dict[str, Convert | None]:
    return {
        # Each hint is `Callable[[], T]`: the serializer is `T`'s.
        name: build_serializer(get_args(_unwrap(hint)[0])[-1])
        for name, hint in get_type_hints(
            injector_functions_type, include_extras=True
        ).items()
    }

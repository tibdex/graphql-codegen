import ast
from collections.abc import Collection, Iterator, Mapping, Sequence
from functools import cache
from itertools import count
from typing import Final, Self, assert_never, final

_MARK: Final = "\x00"
"""NUL: no identifier holds it, and Python refuses it in source, so that a placeholder left in a name fails to compile."""

_counter: Final = count()


@cache
def is_bindable(name: str, /) -> bool:
    if not name.isidentifier():
        return False

    try:
        # Python itself, rather than a list of keywords that could go stale.
        compile(f"{name} = None", "<name>", "exec")
    except SyntaxError:
        return False

    return True


@final
class PendingSpelling:
    """No name picked in advance is safe, since a schema or a document may use it: an emitter writes a placeholder, settled once its module is complete."""

    def __init__(self, preferred: str, /) -> None:
        assert preferred.isidentifier(), f"Expected `{preferred}` to be an identifier."
        # A class body would mangle a `__` name, such as `__GetData_x` nested in an operation named `_Get`.
        unmangled = (
            f"_{preferred.lstrip('_')}"
            if preferred.startswith("__") and not preferred.endswith("__")
            else preferred
        )
        self._placeholder: Final = f"{_MARK}{next(_counter)}{_MARK}{unmangled}"


@final
class SettledSpelling(str):
    __slots__ = ()

    def __new__(cls, value: str, /) -> Self:
        assert is_bindable(value), f"Expected Python to be able to bind `{value}`."
        return super().__new__(cls, value)


type Spelling = PendingSpelling | SettledSpelling


def ast_str(spelling: Spelling, /) -> str:
    match spelling:
        case SettledSpelling():
            return spelling
        case PendingSpelling():
            return spelling._placeholder
        case _ as never:
            assert_never(never)


def _is_placeholder(value: str, /) -> bool:
    return value.startswith(_MARK)


def _preferred(placeholder: str, /) -> str:
    return placeholder.rsplit(_MARK, 1)[1]


def _strings(node: ast.AST, /) -> Iterator[str]:
    for _, value in ast.iter_fields(node):
        values = value if isinstance(value, list) else [value]

        for item in values:
            if isinstance(item, str):
                yield item
            elif isinstance(item, ast.AST):
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    yield item.value
                else:
                    yield from _strings(item)


def _identifiers(module: ast.Module, /) -> Iterator[str]:
    for child in ast.walk(module):
        match child:
            case ast.Name(id=bound):
                pass
            case ast.alias(name=imported, asname=alias):
                bound = alias or imported.split(".")[0]
            case ast.ClassDef(name=bound) | ast.FunctionDef(name=bound):
                pass
            case _:
                continue

        if not _is_placeholder(bound):
            yield bound


def _rewrite(module: ast.Module, spellings: Mapping[str, SettledSpelling], /) -> None:
    # `global` and `nonlocal` hold lists of names, which generated code never writes.
    for node in ast.walk(module):
        for field, value in ast.iter_fields(node):
            if isinstance(value, str) and value in spellings:
                setattr(node, field, spellings[value])


def settle_module(module: ast.Module, /) -> None:
    """Every name the module loads, stores, or imports is taken, whatever its scope, so that a placeholder never clashes with a GraphQL name."""
    fixed = set(_identifiers(module))
    # In creation order.
    placeholders = sorted(
        {value for value in _strings(module) if _is_placeholder(value)},
        key=lambda placeholder: int(placeholder.split(_MARK)[1]),
    )
    spellings: dict[str, SettledSpelling] = {}

    for placeholder in placeholders:
        spelling = next(
            candidate
            for candidate in candidates(_preferred(placeholder))
            if candidate not in fixed
        )
        fixed.add(spelling)
        spellings[placeholder] = spelling

    _rewrite(module, spellings)


def candidates(name: str, /) -> Iterator[SettledSpelling]:
    yield SettledSpelling(name if is_bindable(name) else f"{name}_")
    yield from (SettledSpelling(f"{name}_{suffix}") for suffix in count(1))


def settle_names(
    identifiers: Sequence[str], /, *, taken: Collection[SettledSpelling]
) -> dict[str, SettledSpelling]:
    assert all(identifier.isidentifier() for identifier in identifiers), (
        f"Expected only identifiers, but got {identifiers}."
    )
    used = set(taken)
    spellings: dict[str, SettledSpelling] = {}

    for name in sorted(
        identifiers,
        # Bindable names first, so that a renamed one never takes a name spelled as is.
        key=lambda name: not is_bindable(name),
    ):
        spelling = next(
            candidate for candidate in candidates(name) if candidate not in used
        )
        used.add(spelling)
        spellings[name] = spelling

    return {name: spellings[name] for name in identifiers}

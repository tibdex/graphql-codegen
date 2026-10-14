import builtins
from dataclasses import dataclass
from typing import final

from graphql_codegen._generator.spelling import is_bindable


@final
@dataclass(frozen=True, kw_only=True)
class Builtin:
    name: str


@final
@dataclass(frozen=True, kw_only=True)
class Import:
    level: int
    """As `ast.ImportFrom.level`: 0 for an absolute name, 2 or more for one relative to the package's directory."""

    module: str | None
    name: str


type Reference = Builtin | Import


def parse_dotted_name(dotted_name: str, /) -> Reference:
    level = len(dotted_name) - len(dotted_name.lstrip("."))
    *modules, last = dotted_name[level:].split(".")

    # One dot would point inside the generated package, which holds no user code.
    if (
        level == 1
        or not all(is_bindable(part) for part in (*modules, last))
        or (level == 0 and not modules and not hasattr(builtins, last))
    ):
        raise ValueError(
            f"Expected a builtin (`str`), an absolute dotted name (`decimal.Decimal`), or a path relative to the generated package's directory (`..scalar.decode_datetime`), but got `{dotted_name}`."
        )

    if level == 0 and not modules:
        return Builtin(name=last)

    return Import(level=level, module=".".join(modules) or None, name=last)

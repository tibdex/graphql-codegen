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
    """Like :attr:`ast.ImportFrom.level`: 0 for an absolute path, and 2 or more for one relative to the directory the package is written to."""

    module: str | None
    name: str


type Reference = Builtin | Import


def parse_dotted_name(dotted_name: str, /) -> Reference:
    """A bare name, such as :class:`int`, is a builtin.

    A dotted name is absolute, or starts with `..` to be relative to the directory the package is written to: `..scalars.decode` is `scalars.decode` next to it.
    A dotted name starting with one dot would name a module of the generated package, which is not the project's to write in.
    """
    level = len(dotted_name) - len(dotted_name.lstrip("."))
    *modules, last = dotted_name[level:].split(".")

    if (
        level == 1
        or not all(is_bindable(part) for part in (*modules, last))
        or (level == 0 and not modules and not hasattr(builtins, last))
    ):
        raise ValueError(
            f"Expected an absolute dotted name, such as `datetime.datetime`, a dotted name relative to the package's directory, such as `..scalars.decode`, or a builtin, such as `int`, but got `{dotted_name}`."
        )

    if level == 0 and not modules:
        return Builtin(name=last)

    return Import(level=level, module=".".join(modules) or None, name=last)

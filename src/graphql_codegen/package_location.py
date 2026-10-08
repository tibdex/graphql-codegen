import posixpath
from dataclasses import dataclass
from functools import cached_property
from pathlib import PurePosixPath
from typing import final

from graphql_codegen._generator.spelling import is_bindable


@final
@dataclass(frozen=True, kw_only=True)
class PackageLocation:
    """Where the generated package lives."""

    module_root: PurePosixPath
    """Where the package's top-level package starts."""

    package: str
    """The package's dotted name, which a module outside it imports it by."""

    def __post_init__(self) -> None:
        # Parsed eagerly, so that no location holds a package it cannot use.
        _ = self._parts

    @cached_property
    def _parts(self) -> tuple[str, ...]:
        """The names the package's dotted name holds."""
        parts = tuple(self.package.split("."))

        if not all(is_bindable(part) for part in parts):
            raise ValueError(
                f"Expected the package to be a dotted name, such as `my_app._graphql`, but got `{self.package}`."
            )

        return parts

    @property
    def _package_directory(self) -> PurePosixPath:
        return PurePosixPath(
            posixpath.normpath(self.module_root.joinpath(*self._parts))
        )

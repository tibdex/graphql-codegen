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
    """The directory the package's dotted name starts from."""

    package: str
    """The package's dotted name, such as `my_app._graphql`."""

    def __post_init__(self) -> None:
        # Fail on a bad name now, not at first use.
        _ = self._parts

    @cached_property
    def _parts(self) -> tuple[str, ...]:
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

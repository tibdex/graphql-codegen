from dataclasses import dataclass
from functools import cached_property
from pathlib import PurePosixPath
from typing import final

from graphql_codegen.package_location import PackageLocation


@final
@dataclass(frozen=True, kw_only=True)
class DocumentSiblingModule:
    """The module holding a document's operations and fragments, next to the document.

    The schema types stay in the generated package, so that their import paths do not depend on which documents use them.
    A fragment spread from another document is imported from that document's module.
    """

    name: str
    """Its name, with `{document}` standing for the document's file stem, such as `_{document}_gql`."""

    package_location: PackageLocation
    """Where the generated package is, for the module to import it."""

    def __post_init__(self) -> None:
        # Fail on a bad name now, not at first use.
        _ = self._affixes

    @cached_property
    def _affixes(self) -> tuple[str, str]:
        before, placeholder, after = self.name.partition("{document}")

        if (
            not placeholder
            or "{document}" in after
            or not f"{before}x{after}".isidentifier()
        ):
            raise ValueError(
                f"Expected the module's name to be an identifier holding `{{document}}` once, such as `_{{document}}_gql`, but got `{self.name}`."
            )

        return before, after

    def _module_name(self, document: PurePosixPath, /) -> str:
        before, after = self._affixes
        stem = document.stem

        # `_{document}_gql` with the stem `_cube_` makes `_cube_gql`, not `__cube__gql`.
        if before.endswith("_"):
            stem = stem.lstrip("_")
        if after.startswith("_"):
            stem = stem.rstrip("_")

        return f"{before}{stem}{after}"

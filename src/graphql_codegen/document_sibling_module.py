from dataclasses import dataclass
from functools import cached_property
from pathlib import PurePosixPath
from typing import final

from graphql_codegen.package_location import PackageLocation


@final
@dataclass(frozen=True, kw_only=True)
class DocumentSiblingModule:
    """The module holding a document's operations and fragments, in the document's directory.

    The schema types they reference deliberately stay in the generated package, so that their import paths never depend on which documents use them.
    A fragment spread from another document is imported from its own document's module.
    """

    name: str
    """Its name, such as ``_{document}_gql``, in which ``{document}`` is the document's file stem, the whole then settled as a module name."""

    package_location: PackageLocation
    """Where the package lives, which the module imports it from."""

    def __post_init__(self) -> None:
        # Parsed eagerly, so that no sibling module holds a name it cannot use.
        _ = self._affixes

    @cached_property
    def _affixes(self) -> tuple[str, str]:
        """What the name puts ``(before, after)`` ``{document}``."""
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
        """The name of *document*'s module, before it is settled as a module name.

        An underscore the name puts next to ``{document}`` merges with the stem's own.
        """
        before, after = self._affixes
        stem = document.stem

        # `_{document}_gql` with the stem `_cube_` makes `_cube_gql`, not `__cube__gql`.
        if before.endswith("_"):
            stem = stem.lstrip("_")
        if after.startswith("_"):
            stem = stem.rstrip("_")

        return f"{before}{stem}{after}"

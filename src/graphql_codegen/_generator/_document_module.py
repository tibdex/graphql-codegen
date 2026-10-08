import posixpath
from collections.abc import Sequence
from pathlib import PurePosixPath

from graphql_codegen._generator._imports import DOCUMENT_PACKAGE
from graphql_codegen._generator.spelling import candidates
from graphql_codegen.document_sibling_module import DocumentSiblingModule


def _identifier(stem: str, /) -> str:
    identifier = "".join(
        character if f"_{character}".isidentifier() else "_" for character in stem
    )
    return identifier if identifier.isidentifier() else f"_{identifier}"


def document_modules(
    documents: Sequence[PurePosixPath],
    /,
    *,
    sibling_module: DocumentSiblingModule | None,
) -> dict[PurePosixPath, PurePosixPath]:
    """A module's name depends on its document alone, so that adding a document never renames another's module, which code imports types from: two documents whose modules would be one file are an error rather than one of them getting a suffix.

    It is relative to the package's directory for a module in the package, and in the documents' own terms for a sibling one.
    """
    by_file: dict[tuple[PurePosixPath, str], PurePosixPath] = {}
    modules: dict[PurePosixPath, PurePosixPath] = {}

    for document in sorted(documents):
        if sibling_module is None:
            directory, name = PurePosixPath(DOCUMENT_PACKAGE), document.stem
        else:
            directory = PurePosixPath(posixpath.normpath(document.parent))
            name = sibling_module._module_name(document)

        spelling = next(candidates(_identifier(name)))
        path = directory / f"{spelling}.py"

        if spelling == "__init__":
            raise ValueError(
                f"Expected `{document}` to name a module, but `__init__` is its package's own."
            )

        # Some file systems ignore case.
        if (other := by_file.get((directory, spelling.casefold()))) is not None:
            raise ValueError(
                f"Expected `{other}` and `{document}` to have modules of their own, but both would be `{path}`, case aside."
            )

        by_file[directory, spelling.casefold()] = document
        modules[document] = path

    return modules

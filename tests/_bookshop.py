import shutil
from pathlib import Path
from typing import Final

BOOKSHOP_DIRECTORY: Final = Path(__file__).parents[1] / "bookshop"
BOOKSHOP_GENERATED_PACKAGE_DIRECTORY: Final = BOOKSHOP_DIRECTORY / "graphql"


def copy_bookshop(directory: Path, /) -> None:
    for path in [
        BOOKSHOP_DIRECTORY / "schema.graphqls",
        *BOOKSHOP_DIRECTORY.glob("*.graphql"),
    ]:
        shutil.copy(path, directory)

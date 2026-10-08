import shutil
from pathlib import Path
from typing import Final

BOOKSHOP_DIRECTORY: Final = Path(__file__).parents[1] / "bookshop"


def copy_bookshop(directory: Path, /) -> None:
    for path in [
        BOOKSHOP_DIRECTORY / "schema.graphqls",
        *BOOKSHOP_DIRECTORY.glob("*.graphql"),
    ]:
        shutil.copy(path, directory)

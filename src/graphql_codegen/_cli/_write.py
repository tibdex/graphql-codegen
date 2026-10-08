from collections.abc import Mapping
from pathlib import Path, PurePosixPath


def write(directory: Path, files: Mapping[PurePosixPath, bytes], /) -> None:
    """Write *files*, keyed by their paths relative to *directory*.

    - A file already holding its content is left untouched: file watchers do not see it change.
    - Any other file at one of the paths is overwritten: version control can revert it if that was a mistake.
    - No other file is touched: orphaned document modules are left behind, which is harmless if the output is gitignored.

    """
    for relative_path, content in files.items():
        path = directory / relative_path

        existing = path.read_bytes() if path.is_file() else None

        if existing == content:
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

from collections.abc import Mapping
from pathlib import Path, PurePosixPath


def write(directory: Path, files: Mapping[PurePosixPath, bytes], /) -> None:
    """A file already holding its content is left untouched, so that file watchers do not fire.

    No other file is touched either: a stale document module stays, which is harmless when the output is gitignored.
    """
    for relative_path, content in files.items():
        path = directory / relative_path

        existing = path.read_bytes() if path.is_file() else None

        if existing == content:
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._bookshop import BOOKSHOP_DIRECTORY


@pytest.mark.parametrize(
    "command",
    [
        pytest.param([sys.executable, "-m", "graphql_codegen"], id="python -m"),
        # Next to the interpreter, as `uv run` finds it: the script `pyproject.toml` declares.
        pytest.param(
            [shutil.which("graphql-codegen", path=Path(sys.executable).parent)],
            id="script",
        ),
    ],
)
def test_the_cli_generates_the_committed_bookshop_client(
    command: list[str], tmp_path: Path
) -> None:
    # What its config generates: the package and the modules next to its documents.
    generated = ("client", "*_graphql.py")

    for pattern in generated:
        # Matching nothing, a pattern would let the copy hold what the command must write.
        assert any(BOOKSHOP_DIRECTORY.glob(pattern)), pattern

    directory = tmp_path / "bookshop"
    shutil.copytree(
        BOOKSHOP_DIRECTORY,
        directory,
        ignore=shutil.ignore_patterns(*generated),
    )

    completed = subprocess.run(
        [*command, str(directory / "graphql.config.yml")],
        capture_output=True,
        check=False,
        encoding="utf-8",
    )

    assert completed.stdout == ""
    assert completed.returncode == 0, completed.stderr
    assert _files(directory) == _files(BOOKSHOP_DIRECTORY)


def _files(directory: Path, /) -> dict[Path, bytes]:
    return {
        path.relative_to(directory): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }

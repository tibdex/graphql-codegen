import re
from pathlib import Path
from typing import Final

import pytest

_DIRECTORY: Final = Path(__file__).parents[1]
_README: Final = (_DIRECTORY / "README.md").read_text(encoding="utf-8")
_MARKER: Final = re.compile(r"<!-- (?P<kind>file|excerpt): (?P<path>\S+) -->")
_BLOCK: Final = re.compile(
    rf"(?:{_MARKER.pattern}\n\n)?```(?P<language>\w*)\n(?P<code>.*?)\n```",
    flags=re.DOTALL,
)
_BLOCKS: Final = list(_BLOCK.finditer(_README))


@pytest.mark.parametrize(
    "block",
    [
        pytest.param(
            block,
            # A path alone would repeat, since several excerpts may show one file.
            id=f"line {_README.count(chr(10), 0, block.start()) + 1}"
            + (f": {block['path']}" if block["path"] else ""),
        )
        for block in _BLOCKS
    ],
)
def test_a_code_block_is_a_file_of_the_package(block: re.Match[str]) -> None:
    """Each block is a file of the package, verbatim, so that the README shows no code that the tests do not run or type check.

    Only a shell command, which nothing runs, may stand on its own; a marker's path is relative to the README, like a link.

    An excerpt keeps the file's text in order, a blank line or a `# …` one standing for what it leaves out.
    """
    if block["path"] is None:
        assert block["language"] == "shell", (
            "Expected the block to follow a `<!-- file: … -->` or `<!-- excerpt: … -->` marker naming the file it shows."
        )
        return

    source = (_DIRECTORY / block["path"]).read_text(encoding="utf-8")

    if block["kind"] == "file":
        assert block["code"] == source.rstrip("\n"), (
            f"Expected the block to show `{block['path']}` as it is."
        )
        return

    position = 0

    for piece in re.split(r"\n(?: *# …)?\n", block["code"]):
        position = source.find(piece, position)
        assert position != -1, (
            f"Expected `{block['path']}` to hold, after what the excerpt shows before it:\n{piece}"
        )
        position += len(piece)


def test_every_marker_precedes_a_block() -> None:
    assert len(_MARKER.findall(_README)) == sum(
        block["path"] is not None for block in _BLOCKS
    )

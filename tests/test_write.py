import os
from pathlib import Path, PurePosixPath

from graphql_codegen._cli._write import write


def test_a_file_already_holding_its_content_is_left_untouched(tmp_path: Path) -> None:
    unchanged = tmp_path / "unchanged.py"
    changed = tmp_path / "changed.py"
    unchanged.write_bytes(b"same")
    changed.write_bytes(b"old")
    old_time = 1_000_000_000
    os.utime(unchanged, (old_time, old_time))
    os.utime(changed, (old_time, old_time))

    write(
        tmp_path,
        {PurePosixPath("unchanged.py"): b"same", PurePosixPath("changed.py"): b"new"},
    )

    assert unchanged.stat().st_mtime == old_time
    assert changed.read_bytes() == b"new"

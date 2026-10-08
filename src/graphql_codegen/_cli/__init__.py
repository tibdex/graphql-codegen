import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from pathlib import Path
from typing import final

from graphql_codegen._cli._graphql_config import read_graphql_config
from graphql_codegen._cli._write import write
from graphql_codegen._metadata import DISTRIBUTION_NAME
from graphql_codegen.generate import generate


@final
class _Args(Namespace):
    config_path: Path


def _parse_args(argv: Sequence[str] | None, /) -> _Args:
    parser = ArgumentParser(
        prog=DISTRIBUTION_NAME,
        description="Generate the package of every project of a graphql-config file.",
    )
    parser.add_argument(
        "config_path",
        metavar="config",
        type=Path,
        help="the path to the graphql-config file, in YAML, JSON, or TOML",
    )
    return parser.parse_args(argv, namespace=_Args())


def main(argv: Sequence[str] | None = None, /) -> None:
    cli_args = _parse_args(argv)
    generate_args_by_directory = read_graphql_config(cli_args.config_path)
    packages = {
        directory: generate(**args)
        for directory, args in generate_args_by_directory.items()
    }
    for directory, files in packages.items():
        write(directory, files)
        print(f"Wrote {len(files)} files to `{directory}`.", file=sys.stderr)

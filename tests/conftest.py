import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack
from pathlib import Path, PurePosixPath

import pytest
from graphql import GraphQLSchema

from graphql_codegen import generate
from graphql_codegen._cli._graphql_config import read_graphql_config
from graphql_codegen.generate import _Params
from tests._bookshop import BOOKSHOP_DIRECTORY
from tests._graphql_server import GraphqlHandler, answer_graphql
from tests._server import serve


@pytest.fixture(name="_bookshop_args", scope="session")
def bookshop_args_fixture() -> _Params:
    (args,) = read_graphql_config(BOOKSHOP_DIRECTORY / "graphql.config.yml").values()
    return args


@pytest.fixture(name="bookshop_schema", scope="session")
def bookshop_schema_fixture(_bookshop_args: _Params) -> GraphQLSchema:
    return _bookshop_args["schema"]


@pytest.fixture(name="bookshop_files", scope="session")
def files_fixture(_bookshop_args: _Params) -> Mapping[PurePosixPath, bytes]:
    return generate(**_bookshop_args)


@pytest.fixture(name="assert_type_checks", params=["ty", "pyright", "pyrefly"])
def assert_type_checks_fixture(
    request: pytest.FixtureRequest,
) -> Callable[[Path], None]:
    name = request.param
    checker = shutil.which(name, path=str(Path(sys.executable).parent))
    assert checker is not None, (
        f"Expected `{name}` to be installed next to the interpreter, as a development dependency."
    )

    def assert_type_checks(package: Path, /) -> None:
        arguments = {
            "ty": [
                "check",
                "--python",
                sys.executable,
                "--extra-search-path",
                str(package.parent),
            ],
            "pyright": ["--pythonpath", sys.executable],
            # Its default preset for a project without configuration reports none of the mistakes the others catch.
            "pyrefly": [
                "check",
                "--preset",
                "strict",
                "--python-interpreter-path",
                sys.executable,
                "--search-path",
                str(package.parent),
            ],
        }[name]
        result = subprocess.run(
            [checker, *arguments, str(package)],
            capture_output=True,
            check=False,
            cwd=package.parent,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stdout + result.stderr

    return assert_type_checks


@pytest.fixture(name="graphql_server")
def graphql_server_fixture() -> Iterator[Callable[[GraphqlHandler], str]]:
    """Start a local GraphQL server answering as the given function does, returning its endpoint's URL, until the test ends."""
    with ExitStack() as stack:
        yield (
            lambda graphql_handler: (
                stack.enter_context(serve(answer_graphql(graphql_handler))) + "/graphql"
            )
        )

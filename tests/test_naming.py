import ast
import importlib
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path, PurePosixPath
from typing import Final

import pytest
from graphql import build_schema, parse
from typing_extensions import get_type_hints, is_typeddict

from graphql_codegen import Config, generate

_SCHEMA: Final = """
type Query {
  str: Str
  _typing: String
  node: Node
  class(value: String): Str
}

type Mutation {
  set(input: str): Str
}

type Str {
  class: String
  _compat: String
  from: Int
  str: String
  kind: _enum
  none: None
  mood: enum
}

interface Node {
  id: ID
}

type A implements Node {
  id: ID
  _input: String
}

type B implements Node {
  id: ID
}

input _input {
  str: String
  class: Int
  _compat: _input
}

# Before `str`, which references it: pyrefly reads a forward reference to a type named like a builtin as the builtin.
input input {
  x: Int
}

input str {
  value: _input
  other: input
}

enum enum {
  C
}

enum _enum {
  A
  B
}

enum None {
  X
}
"""

_DOCUMENT: Final = """
query _compat { str { class _compat from str kind none mood } }
query str { class(value: "x") { class } }
query Book { _typing }
query BookData { _typing }
query book { _typing }
query __debug__ { _typing }
query class { node { __typename id ... on A { _input } } }
query WithKeywordVariable($class: String) { class(value: $class) { class } }
query UsesFragment { str { ..._input _compat } }
mutation Set($input: str) { set(input: $input) { class } }
fragment _input on Str { class }
"""


@pytest.fixture(name="files", scope="module")
def files_fixture() -> Mapping[PurePosixPath, bytes]:
    """The package of documents built to collide: operations named like helper aliases, builtins, keywords, and `__debug__`, an operation named like another's data type, names differing only by case, and keys named like everything else."""
    return generate(
        document=parse(_DOCUMENT), schema=build_schema(_SCHEMA), config=Config()
    )


@pytest.fixture(name="package_directory", scope="module")
def package_directory_fixture(
    files: Mapping[PurePosixPath, bytes], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("adversarial_package")
    for relative_path, content in files.items():
        path = root / "adversarial" / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    sys.path.insert(0, str(root))
    yield root / "adversarial"
    sys.path.remove(str(root))


def _bound_names(tree: ast.Module, /) -> list[str]:
    names: list[str] = []
    for statement in tree.body:
        match statement:
            case ast.Import() | ast.ImportFrom():
                names.extend(
                    alias.asname or alias.name.split(".")[0]
                    for alias in statement.names
                    # A star binds its module's names, which GraphQL's one type name space keeps apart from the other's.
                    if alias.name != "*"
                )
            case ast.ClassDef(name=bound) | ast.FunctionDef(name=bound):
                names.append(bound)
            case (
                ast.TypeAlias(name=ast.Name(id=bound))
                | ast.AnnAssign(target=ast.Name(id=bound))
            ):
                names.append(bound)
            case ast.Assign(targets=targets):
                names.extend(
                    target.id for target in targets if isinstance(target, ast.Name)
                )
            case _:
                pass
    return names


def test_every_module_binds_each_name_once(
    files: Mapping[PurePosixPath, bytes],
) -> None:
    for path, source in files.items():
        names = _bound_names(ast.parse(source))
        duplicates = {name for name in names if names.count(name) > 1}
        assert not duplicates, f"{path} binds {duplicates} more than once."


@pytest.mark.parametrize(
    ("path", "snippet"),
    [
        pytest.param(
            "document/GraphQL_request.py",
            "\n_compat: _runtime.Operation[",
            id="an operation named like a helper alias keeps its name",
        ),
        pytest.param(
            "document/GraphQL_request.py",
            "from ..runtime import _compat as _compat_1\n",
            id="the helper alias stepping aside",
        ),
        pytest.param(
            "document/GraphQL_request.py",
            "\nstr: _runtime.Operation[",
            id="so does one named like a builtin",
        ),
        pytest.param(
            "document/GraphQL_request.py",
            "\nBookData: _runtime.Operation[",
            id="and one named like another's data type",
        ),
        pytest.param(
            "document/GraphQL_request.py",
            "\nclass BookData_1(",
            id="that data type stepping aside",
        ),
        pytest.param(
            "document/GraphQL_request.py",
            "\nbook: _runtime.Operation[",
            id="two names differing only by case each keep their own, since a module is not a file",
        ),
        pytest.param(
            "document/GraphQL_request.py",
            "\nclass_: _runtime.Operation[",
            id="a keyword, which cannot be bound, gets PEP 8's trailing underscore",
        ),
        pytest.param(
            "document/GraphQL_request.py",
            "\n__debug___: _runtime.Operation[",
            id="and so does `__debug__`, the one other name that cannot be",
        ),
        pytest.param(
            "schema/__init__.py",
            "from .enum_1 import *\nfrom .input_1 import *\n",
            id="and a schema module for the types named like it",
        ),
    ],
)
def test_a_public_name_is_exact_or_pep_8_escaped_and_an_added_one_steps_aside(
    path: str, snippet: str, files: Mapping[PurePosixPath, bytes]
) -> None:
    assert snippet in files[PurePosixPath(path)].decode()


def test_the_package_imports_and_every_type_resolves(package_directory: Path) -> None:
    modules = [
        ".".join(
            ("adversarial", *path.relative_to(package_directory).with_suffix("").parts)
        ).removesuffix(".__init__")
        for path in package_directory.rglob("*.py")
    ]
    operations: set[str] = set()
    # The package's own copy: the library's classes are not the ones it instantiates.
    runtime = importlib.import_module("adversarial.runtime")
    reflection = importlib.import_module("adversarial.runtime._reflection")

    for module_name in modules:
        module = importlib.import_module(module_name)

        for value in vars(module).values():
            if is_typeddict(value):
                # Resolves every annotation, which fails if a key shadowed a name it uses.
                get_type_hints(value, include_extras=True)
            elif isinstance(value, runtime.Operation):
                operations.add(value._name)
                reflection.build_parser(value._data_type, partial=False)
                reflection.build_serializer(value._variables_type)

    assert len(operations) == 10


def test_the_package_type_checks(
    package_directory: Path, assert_type_checks: Callable[[Path], None]
) -> None:
    assert_type_checks(package_directory)

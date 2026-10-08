from graphql import specified_scalar_types

from graphql_codegen._generator._annotation import BUILT_IN_SCALARS


def test_every_scalar_graphql_core_specifies_has_a_python_type() -> None:
    assert BUILT_IN_SCALARS.keys() == specified_scalar_types.keys()

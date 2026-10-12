from graphql import GraphQLError, GraphQLFormattedError, Source, SourceLocation

from graphql_codegen.runtime import GraphqlError, Location


def test_an_error_has_the_keys_graphql_core_formats_one_with() -> None:
    # Only `message` differs: required by the spec, optional in graphql-core.
    assert GraphqlError.__required_keys__ | GraphqlError.__optional_keys__ == (
        GraphQLFormattedError.__required_keys__
        | GraphQLFormattedError.__optional_keys__
    )


def test_a_location_has_the_keys_graphql_core_formats_one_with() -> None:
    assert Location.__required_keys__ | Location.__optional_keys__ == set(
        SourceLocation(line=1, column=1).formatted
    )


def test_an_error_graphql_core_formats_is_one_the_runtime_reads() -> None:
    # Key by key, so that the type checkers compare each value type.
    formatted = GraphQLError(
        "boom",
        source=Source("query Q { book }"),
        positions=[10],
        path=["book"],
        extensions={"code": "NOT_FOUND"},
    ).formatted
    error: GraphqlError = {
        "message": formatted["message"],
        "locations": formatted["locations"],
        "path": formatted["path"],
        "extensions": formatted["extensions"],
    }

    assert error == formatted

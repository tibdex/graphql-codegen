from graphql import GraphQLError, GraphQLFormattedError, Source, SourceLocation

from graphql_codegen.runtime import Error, Location


def test_an_error_has_the_keys_graphql_core_formats_one_with() -> None:
    """Only which are required differs: the spec requires `message`, which graphql-core leaves optional."""
    assert Error.__required_keys__ | Error.__optional_keys__ == (
        GraphQLFormattedError.__required_keys__
        | GraphQLFormattedError.__optional_keys__
    )


def test_a_location_has_the_keys_graphql_core_formats_one_with() -> None:
    assert Location.__required_keys__ | Location.__optional_keys__ == set(
        SourceLocation(line=1, column=1).formatted
    )


def test_an_error_graphql_core_formats_is_one_a_client_reads() -> None:
    """Built key by key, so that type checkers check that each of graphql-core's value types is one ours accepts."""
    formatted = GraphQLError(
        "boom",
        source=Source("query Q { book }"),
        positions=[10],
        path=["book"],
        extensions={"code": "NOT_FOUND"},
    ).formatted
    error: Error = {
        "message": formatted["message"],
        "locations": formatted["locations"],
        "path": formatted["path"],
        "extensions": formatted["extensions"],
    }

    assert error == formatted

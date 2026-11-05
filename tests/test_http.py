import pytest

from spartan_graphql.runtime.http import is_graphql_response


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        pytest.param(
            "application/graphql-response+json", True, id="the spec's media type"
        ),
        pytest.param(
            "application/graphql-response+json; charset=utf-8",
            True,
            id="with a parameter",
        ),
        pytest.param(
            " Application/GraphQL-Response+JSON ",
            True,
            id="in another case, with spaces around, since media types ignore both",
        ),
        pytest.param("application/json", False, id="the legacy media type"),
        pytest.param(None, False, id="no header"),
    ],
)
def test_a_content_type_tells_a_graphql_response(
    *, content_type: str | None, expected: bool
) -> None:
    assert is_graphql_response(content_type=content_type) == expected

# Copied into each generated package, where this line becomes a `DO NOT EDIT` header.

"""The parts of the GraphQL over HTTP spec (https://http-spec.graphql.org/draft) applicable to the client side."""

from typing import Final

GRAPHQL_RESPONSE_JSON_MEDIA_TYPE: Final = "application/graphql-response+json"
"""The spec's own media type."""

JSON_MEDIA_TYPE: Final = "application/json"
"""The legacy media type of servers predating the spec."""

CONTENT_TYPE: Final = JSON_MEDIA_TYPE
"""The media type of a request body."""

ACCEPT: Final = f"{GRAPHQL_RESPONSE_JSON_MEDIA_TYPE}, {JSON_MEDIA_TYPE};q=0.9"
"""Ask for the spec's media type, and accepts the legacy one from a server without it."""


def is_graphql_response(*, content_type: str | None) -> bool:
    """Whether the body is a GraphQL response, whatever the status.

    Responses with a 4xx or 5xx status could originate from non-GraphQL intermediary servers and middleware handling failures.
    A response with the spec's media type is a well-formed GraphQL response independent of the HTTP status code.
    """
    return (
        content_type is not None
        and _media_type(content_type) == GRAPHQL_RESPONSE_JSON_MEDIA_TYPE
    )


def _media_type(content_type: str, /) -> str:
    """The media type of a `Content-Type` value, without its parameters such as `charset`."""
    return content_type.partition(";")[0].strip().lower()

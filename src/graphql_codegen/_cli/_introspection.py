from collections.abc import Mapping
from http.client import HTTPResponse
from mimetypes import types_map
from typing import Final, Literal, TypeAlias
from urllib.request import Request as HTTPRequest, urlopen

from graphql import (
    IntrospectionQuery,
    build_client_schema,
    get_introspection_query,
    print_schema,
)

from graphql_codegen._cli._introspection_graphql import (
    FieldNames,
    IntrospectionProbe,
    IntrospectionProbeData,
)
from graphql_codegen._metadata import DISTRIBUTION_NAME, METADATA
from graphql_codegen.runtime import Operation, Request

_JSON: Final = types_map[".json"]
_GRAPHQL_RESPONSE: Final = "application/graphql-response+json"


def _field_names(type_: FieldNames, /) -> frozenset[str]:
    return frozenset(field["name"] for field in type_["fields"])


# Not a `type` statement, which could not be called to build the operation.
_IntrospectionOperation: TypeAlias = Operation[  # noqa: UP040
    Literal["query"], dict[str, object], IntrospectionQuery
]


def _introspection_operation(
    probe: IntrospectionProbeData, /
) -> _IntrospectionOperation:
    type_fields = _field_names(probe["type"])
    document = get_introspection_query(
        specified_by_url="specifiedByURL" in type_fields,
        directive_is_repeatable="isRepeatable" in _field_names(probe["directive"]),
        schema_description="description" in _field_names(probe["schema"]),
        input_value_deprecation="isDeprecated" in _field_names(probe["inputValue"]),
        one_of="isOneOf" in type_fields,
    )
    return _IntrospectionOperation(
        operation_type="query",
        name="IntrospectionQuery",
        document=document,
        variables_type=dict[str, object],
        # graphql-core's `IntrospectionQuery` uses `DirectiveLocation`, which it imports only `if TYPE_CHECKING:`.
        data_type=dict[str, object],  # ty: ignore[invalid-argument-type]
    )


def fetch_sdl(url: str, /, *, headers: Mapping[str, str]) -> str:
    def send[Data](request: Request[Literal["query"], Data], /) -> Data:
        prepared = request.prepare()
        request_body = bytes(prepared)
        http_request = HTTPRequest(  # noqa: S310
            url,
            headers={
                "Accept": f"{_GRAPHQL_RESPONSE}, {_JSON}",
                "Content-Type": _JSON,
                "User-Agent": f"{DISTRIBUTION_NAME}/{METADATA['Version']}",
                **headers,
            },
            data=request_body,
        )

        response = urlopen(  # noqa: S310
            http_request,
            timeout=30.0,  # graphql-config has no option for it.
        )
        # The only response an `http:` or `https:` URL gives.
        assert isinstance(response, HTTPResponse)

        with response:
            response_body = response.read()

        return prepared.parse(response_body)

    probe = send(IntrospectionProbe({}))
    introspection_operation = _introspection_operation(probe)
    introspection_data = send(introspection_operation({}))
    return print_schema(build_client_schema(introspection=introspection_data))

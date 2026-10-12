from collections.abc import Mapping
from dataclasses import dataclass
from typing import final

from graphql import (
    GraphQLError,
    GraphQLInputObjectType,
    GraphQLInterfaceType,
    GraphQLNamedType,
    GraphQLScalarType,
    GraphQLSchema,
    get_named_type,
    is_specified_scalar_type,
)


@final
@dataclass(frozen=True, kw_only=True)
class StructTypes:
    payload_field_name: str

    input_type_names: Mapping[str, str]


def parse_struct_types(
    schema: GraphQLSchema, struct_interface_name: str, /
) -> StructTypes:
    interface = schema.type_map.get(struct_interface_name)

    if not isinstance(interface, GraphQLInterfaceType):
        # A config value naming nothing in the schema, not a mistyped one.
        raise ValueError(f"Found no interface named `{struct_interface_name}`.")  # noqa: TRY004

    match list(interface.fields.items()):
        case [(payload_field_name, payload_field)] if isinstance(
            payload_type := get_named_type(payload_field.type), GraphQLScalarType
        ) and not is_specified_scalar_type(payload_type):
            pass
        case _:
            raise GraphQLError(
                f"Expected `{struct_interface_name}` to have a single field, the payload, of a custom scalar type.",
                nodes=interface.ast_node,
            )

    input_type_names: dict[str, str] = {}

    for object_type in schema.get_possible_types(interface):
        input_name = object_type.name.removesuffix(struct_interface_name)

        if input_name == object_type.name or not isinstance(
            schema.type_map.get(input_name), GraphQLInputObjectType
        ):
            raise GraphQLError(
                f"Expected `{object_type.name}`, which implements `{struct_interface_name}`, to be named after an input type followed by `{struct_interface_name}`.",
                nodes=object_type.ast_node,
            )

        input_type_names[object_type.name] = input_name

    return StructTypes(
        payload_field_name=payload_field_name, input_type_names=input_type_names
    )


def get_struct_input_name(
    parent_type: GraphQLNamedType,
    field_name: str,
    /,
    *,
    struct_types: StructTypes | None,
) -> str | None:
    """The payload stays under its field's key rather than replacing the struct, so that the data's type mirrors the document and only a response selecting it pays for a conversion."""
    if struct_types is None or field_name != struct_types.payload_field_name:
        return None

    return struct_types.input_type_names.get(parent_type.name)

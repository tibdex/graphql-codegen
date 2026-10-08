from graphql import (
    DirectiveLocation,
    GraphQLDirective,
    GraphQLNonNull,
    GraphQLNullableType,
    GraphQLSchema,
    validate_schema,
)


def prepare_schema(
    schema: GraphQLSchema, /, *, non_null_directive_name: str | None
) -> GraphQLSchema:
    """Declares the client directive, if any, so that documents using it validate."""
    if non_null_directive_name is not None:
        schema = _declare_directive(schema, name=non_null_directive_name)

    # The schema operations are validated against, whose result graphql-core's `validate()` then reuses.
    if errors := validate_schema(schema):
        raise ExceptionGroup("The schema is invalid", errors)

    return schema


def _declare_directive(schema: GraphQLSchema, /, *, name: str) -> GraphQLSchema:
    if schema.get_directive(name) is not None:
        raise ValueError(
            f"Expected the schema to declare no `@{name}`, the name of the client directive."
        )

    directive = GraphQLDirective(
        name,
        locations=[DirectiveLocation.FIELD],
        description="Assert that this field is not null, even though the schema allows it to be.",
    )

    # A new dictionary on each call, so setting a key of it leaves *schema* alone.
    kwargs = schema.to_kwargs()
    kwargs["directives"] = (*schema.directives, directive)
    return GraphQLSchema(**kwargs)


def as_non_null[Nullable: GraphQLNullableType](
    type_: Nullable | GraphQLNonNull[Nullable], /
) -> GraphQLNonNull[Nullable]:
    return type_ if isinstance(type_, GraphQLNonNull) else GraphQLNonNull(type_)

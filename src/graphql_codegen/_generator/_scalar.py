import ast
from collections.abc import Mapping
from typing import assert_never

from graphql import GraphQLScalarType, GraphQLSchema

from graphql_codegen._generator._ast_nodes import name, qualified, subscript, type_alias
from graphql_codegen._generator._naming import BUILTINS, REFLECTION, TYPING
from graphql_codegen._generator.dotted_name import Builtin, Import, Reference
from graphql_codegen._generator.spelling import (
    PendingSpelling,
    SettledSpelling,
    ast_str,
)
from graphql_codegen.scalar import Scalar


def check_scalars(scalars: Mapping[str, Scalar], /, *, schema: GraphQLSchema) -> None:
    for scalar_name in sorted(scalars):
        if not isinstance(schema.type_map.get(scalar_name), GraphQLScalarType):
            # A config value naming nothing in the schema, not a mistyped one.
            raise ValueError(f"Expected `{scalar_name}` to be a scalar of the schema.")  # noqa: TRY004


def emit_scalars(
    scalars: Mapping[str, Scalar], /, *, spellings: Mapping[str, SettledSpelling]
) -> list[ast.stmt]:
    imported: dict[Import, PendingSpelling] = {}

    def reference(path: Reference, /) -> ast.expr:
        match path:
            case Builtin(name=builtin_name):
                return qualified(BUILTINS, builtin_name)
            case Import(name=imported_name):
                return name(
                    imported.setdefault(path, PendingSpelling(f"_{imported_name}"))
                )
            case _ as never:
                assert_never(never)

    body: list[ast.stmt] = []

    for scalar_name, scalar in sorted(scalars.items()):
        if scalar.codec is None:
            value = reference(scalar._type_reference)
        else:
            value = subscript(
                qualified(TYPING, "Annotated"),
                reference(scalar._type_reference),
                ast.Call(
                    func=qualified(REFLECTION, "Codec"),
                    args=[],
                    keywords=[
                        ast.keyword(
                            arg="decode",
                            value=reference(scalar.codec._decode_reference),
                        ),
                        ast.keyword(
                            arg="encode",
                            value=reference(scalar.codec._encode_reference),
                        ),
                    ],
                ),
            )

        body.append(type_alias(spellings[scalar_name], value))

    imports = [
        ast.ImportFrom(
            module=import_.module,
            names=[ast.alias(name=import_.name, asname=ast_str(spelling))],
            level=import_.level,
        )
        for import_, spelling in imported.items()
    ]
    return [*imports, *body]

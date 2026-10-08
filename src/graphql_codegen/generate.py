from pathlib import PurePosixPath
from typing import Unpack

from graphql import DocumentNode, GraphQLSchema

from graphql_codegen._generator.package import PackageEmitter
from graphql_codegen.config import Config
from graphql_codegen.runtime._compat import TypedDict


class _Params(TypedDict, closed=True):
    document: DocumentNode
    schema: GraphQLSchema
    config: Config


def generate(**args: Unpack[_Params]) -> dict[PurePosixPath, bytes]:
    """Pure function returning the content of each file of the generated package by its path."""
    return PackageEmitter(**args).emit()

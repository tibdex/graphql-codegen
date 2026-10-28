import ast
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from functools import cached_property
from graphlib import TopologicalSorter
from importlib.resources import files as resource_files
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Final, assert_never, final

from graphql import (
    DocumentNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLSchema,
    Node,
    OperationDefinitionNode,
    Visitor,
    visit,
)

from graphql_codegen import runtime
from graphql_codegen._generator._annotation import AnnotationBuilder
from graphql_codegen._generator._ast_nodes import (
    FUTURE_ANNOTATIONS,
    import_as,
    module,
    name,
    qualified,
    unparse,
)
from graphql_codegen._generator._data_type import DataTypeEmitter
from graphql_codegen._generator._document import (
    get_transitively_spread_fragments,
    insert_typename,
    parse_operations,
)
from graphql_codegen._generator._document_module import document_modules
from graphql_codegen._generator._imports import (
    DOCUMENT_PACKAGE,
    INJECTION_MODULE,
    RUNTIME_PACKAGE,
    SCHEMA_MODULE,
    GeneratedModule,
    OutsideModule,
    PackageModule,
    helper_imports,
)
from graphql_codegen._generator._injector import (
    emit_injector_module,
    get_injected_types,
    get_injections,
)
from graphql_codegen._generator._merge import print_merge_template
from graphql_codegen._generator._naming import (
    OperationSpellings,
    operation_spellings,
)
from graphql_codegen._generator._operation import (
    description_text,
    emit_operation_constant,
    emit_variables_type,
    strip_for_server,
)
from graphql_codegen._generator._scalar import check_scalars
from graphql_codegen._generator._schema import prepare_schema
from graphql_codegen._generator._schema_type import (
    emit_schema_module,
    get_reached_type_names,
)
from graphql_codegen._generator._selection import SelectionResolver
from graphql_codegen._generator._structs import parse_struct_types
from graphql_codegen._generator.spelling import (
    PendingSpelling,
    SettledSpelling,
    ast_str,
    is_bindable,
    settle_module,
    settle_names,
)
from graphql_codegen._metadata import GENERATED_FILE_COMMENT_PREFIX
from graphql_codegen.config import Config
from graphql_codegen.document_sibling_module import DocumentSiblingModule

_DO_NOT_EDIT: Final = "DO NOT EDIT."
_GENERATED_FILE_COMMENT: Final = f"{GENERATED_FILE_COMMENT_PREFIX}. {_DO_NOT_EDIT}\n"


def _generated(source: str, /) -> str:
    return (
        f"{_GENERATED_FILE_COMMENT}\n{source}"
        if source.strip()
        else _GENERATED_FILE_COMMENT
    )


def _names_by_document(
    definitions: Mapping[str, OperationDefinitionNode | FragmentDefinitionNode],
    /,
    *,
    module_root: PurePosixPath | None,
) -> dict[PurePosixPath, list[str]]:
    names: dict[PurePosixPath, list[str]] = {}

    for definition_name, definition in definitions.items():
        # graphql-core's name for a string parsed without a source.
        document = PurePosixPath(
            "GraphQL request" if definition.loc is None else definition.loc.source.name
        )

        if module_root is not None and (
            definition.loc is None
            or document.is_absolute() != module_root.is_absolute()
        ):
            raise ValueError(
                "Expected every operation and fragment to come from a source named by its path, absolute or relative to the same directory as the module root, so that its module can go next to its document."
            )

        names.setdefault(document, []).append(definition_name)

    return names


def _import_module(dotted_name: str, /, *, alias: PendingSpelling) -> ast.stmt:
    """`from a.b import c`, since `import a.b.c as alias` would let a submodule named like the package shadow it."""
    package, _, module_name = dotted_name.rpartition(".")
    return (
        ast.ImportFrom(
            module=package,
            names=[ast.alias(name=module_name, asname=ast_str(alias))],
            level=0,
        )
        if package
        else import_as(module_name, alias)
    )


def _direct_spread_names(node: Node, /) -> set[str]:
    names: set[str] = set()

    class _Visitor(Visitor):
        def enter_fragment_spread(
            self, spread: FragmentSpreadNode, *_args: object
        ) -> None:
            names.add(spread.name.value)

    visit(node, _Visitor())
    return names


def _dotted_module_name(
    path: PurePosixPath, /, *, document: PurePosixPath, module_root: PurePosixPath
) -> str:
    # Outside the module root, the path holds `..`, which no dotted name can.
    parts = path.with_suffix("").relative_to(module_root, walk_up=True).parts

    if all(is_bindable(part) for part in parts):
        return ".".join(parts)

    raise ValueError(
        f"Expected `{document}`'s module, `{path}`, to be importable from the module root, since another document spreads its fragments."
    )


_RUNTIME_MARKER: Final = b"# Copied into each generated package, where this line becomes a `DO NOT EDIT` header.\n"
"""The first line of every runtime module, swapped for the generated file comment."""


def _runtime_copy(source: bytes, /) -> bytes:
    """Swapped line for line, so that coverage maps each line of the copy to the same line of the runtime."""
    assert source.startswith(_RUNTIME_MARKER), (
        "Expected a runtime module to start with its marker."
    )
    return _GENERATED_FILE_COMMENT.encode() + source.removeprefix(_RUNTIME_MARKER)


_RUNTIME_FILES: Final = MappingProxyType(
    {
        PurePosixPath(RUNTIME_PACKAGE, resource.name): _runtime_copy(
            resource.read_bytes()
        )
        for resource in sorted(
            resource_files(runtime).iterdir(), key=lambda resource: resource.name
        )
        if resource.name.endswith(".py")
    }
)
"""Read at import, so that `generate()` reads no file."""


@final
class PackageEmitter:
    def __init__(
        self, *, document: DocumentNode, schema: GraphQLSchema, config: Config
    ) -> None:
        """Everything that can fail is derived here, and the rest on first use."""
        schema = prepare_schema(
            schema, non_null_directive_name=config.non_null_directive_name
        )

        struct_types = (
            None
            if config.struct_interface_name is None
            else parse_struct_types(schema, config.struct_interface_name)
        )

        check_scalars(config.scalars, schema=schema)
        injector_names = frozenset(config.injector_names)
        document = insert_typename(document, schema=schema)
        operations = parse_operations(document, schema=schema)
        fragments = {
            definition.name.value: definition
            for definition in document.definitions
            if isinstance(definition, FragmentDefinitionNode)
        }
        sibling_module = config.document_sibling_module
        module_root = (
            None
            if sibling_module is None
            else sibling_module.package_location.module_root
        )
        operation_names = _names_by_document(operations, module_root=module_root)
        fragment_names = _names_by_document(fragments, module_root=module_root)
        modules = document_modules(
            sorted({*operation_names, *fragment_names}), sibling_module=sibling_module
        )
        operation_spellings = settle_names(sorted(operations), taken=set())

        injections = {
            operation_name: get_injections(
                operation, schema=schema, injector_names=injector_names
            )
            for operation_name, operation in operations.items()
        }

        self._schema: Final = schema
        self._struct_types: Final = struct_types
        self._document: Final = document
        self._document_sibling_module: Final = sibling_module
        self._injector_names: Final = injector_names
        self._non_null_directive_name: Final = config.non_null_directive_name
        self._scalars: Final = config.scalars
        self._operations: Final = operations
        self._operation_spellings: Final = operation_spellings
        self._operation_names_by_document: Final = operation_names
        fragment_documents = {
            fragment_name: fragment_document
            for fragment_document, names in fragment_names.items()
            for fragment_name in names
        }
        self._fragments: Final = fragments
        self._fragment_names_by_document: Final = fragment_names
        self._fragment_documents: Final = fragment_documents
        self._document_modules: Final = modules
        self._imported_module_dotted_names: Final = (
            {} if module_root is None else self._spread_module_names(module_root)
        )
        self._injections: Final = injections
        self._injected_types: Final = get_injected_types(
            injections, injector_names=injector_names
        )

    def _spread_module_names(
        self, module_root: PurePosixPath, /
    ) -> dict[PurePosixPath, str]:
        """The dotted name of each module a document spreads a fragment from."""
        dotted_names: dict[PurePosixPath, str] = {}

        for definitions, names_by_document in (
            (self._operations, self._operation_names_by_document),
            (self._fragments, self._fragment_names_by_document),
        ):
            for document, definition_names in names_by_document.items():
                for definition_name in definition_names:
                    for spread_name in _direct_spread_names(
                        definitions[definition_name]
                    ):
                        spread_document = self._fragment_documents[spread_name]

                        if spread_document != document:
                            dotted_names[spread_document] = _dotted_module_name(
                                self._document_modules[spread_document],
                                document=spread_document,
                                module_root=module_root,
                            )

        return dotted_names

    @cached_property
    def _resolver(self) -> SelectionResolver:
        return SelectionResolver(self._fragments, schema=self._schema)

    @cached_property
    def _type_spellings(self) -> dict[str, SettledSpelling]:
        reached = get_reached_type_names(
            self._document,
            schema=self._schema,
            struct_types=self._struct_types,
            injector_names=self._injector_names,
        )
        return settle_names(sorted({*reached, *self._scalars}), taken=set())

    @cached_property
    def _fragment_spellings(self) -> dict[str, SettledSpelling]:
        return {
            fragment_name: spelling
            for document, names in self._fragment_names_by_document.items()
            for fragment_name, spelling in settle_names(
                sorted(names),
                taken={
                    self._operation_spellings[operation_name]
                    for operation_name in self._operation_names_by_document.get(
                        document, []
                    )
                },
            ).items()
        }

    @cached_property
    def _annotations(self) -> AnnotationBuilder:
        return AnnotationBuilder(type_spellings=self._type_spellings)

    def _module_source(
        self,
        body: Sequence[ast.stmt],
        /,
        *,
        generated_module: GeneratedModule,
        preamble: Sequence[ast.stmt],
    ) -> str:
        imports = helper_imports(body, module=generated_module)
        tree = module([*preamble, *imports, *body])
        settle_module(tree)

        for node in ast.walk(tree):
            # A helper settled like the name it imports would spell a re-export.
            if isinstance(node, ast.alias) and node.asname == node.name:
                node.asname = None

        return unparse(tree)

    @cached_property
    def _data_types(self) -> DataTypeEmitter:
        return DataTypeEmitter(
            schema=self._schema,
            annotations=self._annotations,
            type_spellings=self._type_spellings,
            resolve_fragment=lambda fragment_name: self._resolver.resolve(
                self._fragments[fragment_name]
            ),
            struct_types=self._struct_types,
            non_null_directive_name=self._non_null_directive_name,
        )

    def _fragment_order(self, fragment_names: Sequence[str], /) -> list[str]:
        # A fragment comes after the ones it spreads, since it may inherit their types.
        return list(
            TopologicalSorter(
                {
                    fragment_name: [
                        spread.name.value
                        for spread in get_transitively_spread_fragments(
                            self._fragments[fragment_name], definitions=self._fragments
                        )
                        if spread.name.value in fragment_names
                    ]
                    for fragment_name in fragment_names
                }
            ).static_order()
        )

    def _fragment_types(
        self,
        fragment_name: str,
        /,
        *,
        fragment_reference: Callable[[str], ast.expr],
    ) -> list[ast.stmt]:
        definition = self._fragments[fragment_name]
        selection_set = self._resolver.resolve(definition)
        return self._data_types.emit(
            # The fragment's description over its type's.
            replace(selection_set, description=description)
            if (description := description_text(definition.description))
            else selection_set,
            root_spelling=self._fragment_spellings[fragment_name],
            fragment_reference=fragment_reference,
            inheritable=True,
        )

    def _operation_body(
        self,
        operation_name: str,
        operation: OperationDefinitionNode,
        /,
        *,
        spellings: OperationSpellings,
        fragment_reference: Callable[[str], ast.expr],
    ) -> list[ast.stmt]:
        document = strip_for_server(
            operation,
            fragments=get_transitively_spread_fragments(
                operation, definitions=self._fragments
            ),
            client_directive_names=frozenset()
            if self._non_null_directive_name is None
            else frozenset({self._non_null_directive_name}),
        )
        return [
            *emit_variables_type(
                operation,
                spellings=spellings,
                schema=self._schema,
                annotations=self._annotations,
                injector_names=self._injector_names,
            ),
            *self._data_types.emit(
                # The root type's description says nothing about this operation.
                replace(self._resolver.resolve(operation), description=""),
                root_spelling=spellings.data,
                fragment_reference=fragment_reference,
                inheritable=False,
            ),
            *emit_operation_constant(
                operation,
                graphql_name=operation_name,
                spellings=spellings,
                document=print_merge_template(document),
                injections=self._injections[operation_name],
            ),
        ]

    def _emit_document_module(
        self,
        document: PurePosixPath,
        /,
        *,
        generated_module: GeneratedModule,
    ) -> str:
        operation_names = self._operation_names_by_document.get(document, [])
        fragment_names = self._fragment_names_by_document.get(document, [])
        spellings = operation_spellings(
            [self._operation_spellings[name] for name in operation_names],
            taken=[self._fragment_spellings[name] for name in fragment_names],
        )
        aliases: dict[PurePosixPath, PendingSpelling] = {}

        def fragment_reference(fragment_name: str, /) -> ast.expr:
            spelling = self._fragment_spellings[fragment_name]

            if (
                fragment_document := self._fragment_documents[fragment_name]
            ) == document:
                return name(spelling)

            alias = aliases.setdefault(
                fragment_document,
                PendingSpelling(f"_{self._document_modules[fragment_document].stem}"),
            )
            return qualified(alias, spelling)

        body = [
            *(
                statement
                for fragment_name in self._fragment_order(fragment_names)
                for statement in self._fragment_types(
                    fragment_name, fragment_reference=fragment_reference
                )
            ),
            *(
                statement
                for operation_name in operation_names
                for statement in self._operation_body(
                    operation_name,
                    self._operations[operation_name],
                    spellings=spellings[self._operation_spellings[operation_name]],
                    fragment_reference=fragment_reference,
                )
            ),
        ]
        source = self._module_source(
            [
                *(
                    self._import_document_module(imported, alias=alias)
                    for imported, alias in sorted(aliases.items())
                ),
                *body,
            ],
            generated_module=generated_module,
            preamble=(),
        )
        return f"{GENERATED_FILE_COMMENT_PREFIX} from `{document.name}`. {_DO_NOT_EDIT}\n\n{source}"

    def _import_document_module(
        self, document: PurePosixPath, /, *, alias: PendingSpelling
    ) -> ast.stmt:
        match self._document_sibling_module:
            case None:
                return ast.ImportFrom(
                    module=None,
                    names=[
                        ast.alias(
                            name=self._document_modules[document].stem,
                            asname=ast_str(alias),
                        )
                    ],
                    level=1,
                )
            case DocumentSiblingModule():
                return _import_module(
                    self._imported_module_dotted_names[document], alias=alias
                )
            case _ as never:
                assert_never(never)

    def _emit_document_modules(self) -> dict[PurePosixPath, str]:
        files: dict[PurePosixPath, str]
        generated_module: GeneratedModule

        match self._document_sibling_module:
            case None:
                modules = self._document_modules
                files = {PurePosixPath(DOCUMENT_PACKAGE, "__init__.py"): _generated("")}
                generated_module = PackageModule(path=(DOCUMENT_PACKAGE,))
            case DocumentSiblingModule(package_location=location):
                directory = location._package_directory
                modules = {
                    document: path.relative_to(directory, walk_up=True)
                    for document, path in self._document_modules.items()
                }
                files = {}
                generated_module = OutsideModule(package=location.package)
            case _ as never:
                assert_never(never)

        for document, module_path in modules.items():
            files[module_path] = self._emit_document_module(
                document, generated_module=generated_module
            )

        return files

    def emit(self) -> dict[PurePosixPath, bytes]:
        document_files = self._emit_document_modules()
        files: dict[PurePosixPath, str] = {
            PurePosixPath("__init__.py"): _generated(""),
            PurePosixPath(f"{SCHEMA_MODULE}.py"): _generated(
                self._module_source(
                    emit_schema_module(
                        self._schema,
                        annotations=self._annotations,
                        type_spellings=self._type_spellings,
                        scalars=self._scalars,
                        injector_names=self._injector_names,
                    ),
                    generated_module=PackageModule(path=()),
                    preamble=[FUTURE_ANNOTATIONS],
                )
            ),
            **document_files,
        }

        if self._injected_types:
            files[PurePosixPath(f"{INJECTION_MODULE}.py")] = _generated(
                self._module_source(
                    emit_injector_module(
                        self._injected_types, annotations=self._annotations
                    ),
                    generated_module=PackageModule(path=()),
                    preamble=(),
                )
            )

        return {
            **_RUNTIME_FILES,
            **{path: source.encode() for path, source in files.items()},
        }

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
    constant,
    import_as,
    module,
    name,
    qualified,
    relative_import_from,
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
    SCALAR_MODULE,
    SCHEMA_PACKAGE,
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
    SchemaSpellings,
    operation_spellings,
    schema_spellings,
)
from graphql_codegen._generator._operation import (
    description_text,
    emit_operation_constant,
    emit_variables_type,
    strip_for_server,
)
from graphql_codegen._generator._scalar import check_scalars, emit_scalars
from graphql_codegen._generator._schema import prepare_schema
from graphql_codegen._generator._schema_type import (
    emit_enums,
    emit_inputs,
    emit_schema_package,
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
    """*source* under the generated file comment, separated by a blank line unless it is empty."""
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
    """A document is the path its source is named by.

    When *module_root*, where sibling modules are imported from, is not ``None``, every definition must be located in its terms: that is where its module goes.
    """
    names: dict[PurePosixPath, list[str]] = {}

    for definition_name, definition in definitions.items():
        # graphql-core names a string parsed without a source so too.
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
    """Import the module *dotted_name* from its package, which a submodule named like the package cannot shadow, as `import a.b.c as alias` lets `a`'s attribute `b` do."""
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
    # Outside the module root, a module's path walks up with `..`, which no dotted name holds.
    parts = path.with_suffix("").relative_to(module_root, walk_up=True).parts

    if all(is_bindable(part) for part in parts):
        return ".".join(parts)

    raise ValueError(
        f"Expected `{document}`'s module, `{path}`, to be importable from the module root, since another document spreads its fragments."
    )


_RUNTIME_MARKER: Final = (
    b"# Copied into each generated package, where this line says not to edit it.\n"
)
"""The first line of every runtime module, which the copy swaps for the generated file comment."""


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
"""The runtime, copied so that a generated package depends on nothing but the standard library, and :mod:`typing_extensions` before Python 3.15.

Read once, at import, so that generating reads no file.
"""


@final
class PackageEmitter:
    def __init__(
        self, *, document: DocumentNode, schema: GraphQLSchema, config: Config
    ) -> None:
        """Derive what can fail, so that no emitter holds what it cannot generate from, leaving the rest to first use."""
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
        # The `document` package re-exports every operation and fragment beside its modules, which keep their names, then the operations theirs.
        module_spellings = (
            {SettledSpelling(path.stem) for path in modules.values()}
            if sibling_module is None
            else set()
        )
        operation_spellings = settle_names(sorted(operations), taken=module_spellings)

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
        self._module_spellings: Final = module_spellings
        self._fragment_documents: Final = fragment_documents
        self._document_modules: Final = modules
        self._imported_module_dotted_names: Final = (
            {}
            if module_root is None
            else {
                spread_document: _dotted_module_name(
                    modules[spread_document],
                    document=spread_document,
                    module_root=module_root,
                )
                for definitions, names in (
                    (operations, operation_names),
                    (fragments, fragment_names),
                )
                for definition_document, definition_names in names.items()
                for definition_name in definition_names
                for spread_name in _direct_spread_names(definitions[definition_name])
                if (spread_document := fragment_documents[spread_name])
                != definition_document
            }
        )
        self._injections: Final = injections
        self._injected_types: Final = get_injected_types(
            injections, injector_names=injector_names
        )

    @cached_property
    def _resolver(self) -> SelectionResolver:
        return SelectionResolver(self._fragments, schema=self._schema)

    @cached_property
    def _schema_spellings(self) -> SchemaSpellings:
        return schema_spellings(
            get_reached_type_names(
                self._document,
                schema=self._schema,
                struct_types=self._struct_types,
                injector_names=self._injector_names,
            ),
        )

    @cached_property
    def _scalar_spellings(self) -> dict[str, SettledSpelling]:
        return settle_names(sorted(self._scalars), taken=set())

    @cached_property
    def _fragment_spellings(self) -> dict[str, SettledSpelling]:
        """Settled where their names meet others: in the `document` package, which re-exports them, and in their sibling module otherwise."""
        if self._document_sibling_module is None:
            return settle_names(
                sorted(self._fragments),
                taken={*self._module_spellings, *self._operation_spellings.values()},
            )

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
        return AnnotationBuilder(
            scalar_spellings=self._scalar_spellings,
            schema_spellings=self._schema_spellings,
        )

    def _module_source(
        self,
        body: list[ast.stmt],
        /,
        *,
        generated_module: GeneratedModule,
        preamble: Sequence[ast.stmt],
    ) -> str:
        imports = helper_imports(
            body, module=generated_module, schema_spellings=self._schema_spellings
        )
        tree = module([*preamble, *imports, *body])
        settle_module(tree)

        for node in ast.walk(tree):
            # Settled like the name it imports, a helper's alias would make the import a re-export, as PEP 484 spells one, when a module only exports what it defines.
            if isinstance(node, ast.alias) and node.asname == node.name:
                node.asname = None

        return unparse(tree)

    @cached_property
    def _data_types(self) -> DataTypeEmitter:
        return DataTypeEmitter(
            schema=self._schema,
            annotations=self._annotations,
            schema_spellings=self._schema_spellings,
            resolve_fragment=lambda fragment_name: self._resolver.resolve(
                self._fragments[fragment_name]
            ),
            struct_types=self._struct_types,
            non_null_directive_name=self._non_null_directive_name,
        )

    def _fragment_order(self, fragment_names: Sequence[str], /) -> list[str]:
        """*fragment_names*, each after those of them it spreads, since their types may be its bases."""
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
            # The fragment's own description, when it has one, says more than its type's.
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
            # The server knows nothing of the directive, which is the client's assertion.
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
                # `Query` and `Mutation` describe the schema's entry points, which says nothing about this operation, so the root type's description is dropped rather than repeated on every one.
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
                # After the helpers' imports, which `_module_source()` puts first.
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

    def _emit_document_reexports(
        self, modules: Mapping[PurePosixPath, PurePosixPath], /
    ) -> str:
        """The `document` subpackage's `__init__.py`, re-exporting every operation and fragment by name, since their module also holds the types of their operations' variables and data.

        Its modules are listed in `__lazy_modules__`, which Python 3.15 imports on first use (PEP 810) and earlier versions ignore: a star import could not be lazy.
        """
        stems = sorted((path.stem, document) for document, path in modules.items())
        lazy_modules = ast.Assign(
            targets=[ast.Name(id="__lazy_modules__", ctx=ast.Store())],
            value=ast.List(
                elts=[
                    # Fully qualified from `__name__`, so that the package works wherever it is written.
                    ast.BinOp(
                        left=ast.Name(id="__name__", ctx=ast.Load()),
                        op=ast.Add(),
                        right=constant(f".{stem}"),
                    )
                    for stem, _ in stems
                ],
                ctx=ast.Load(),
            ),
            lineno=0,
        )
        return unparse(
            module(
                [
                    lazy_modules,
                    *(
                        relative_import_from(
                            stem,
                            sorted(
                                [
                                    *(
                                        self._operation_spellings[name]
                                        for name in self._operation_names_by_document.get(
                                            document, []
                                        )
                                    ),
                                    *(
                                        self._fragment_spellings[name]
                                        for name in self._fragment_names_by_document.get(
                                            document, []
                                        )
                                    ),
                                ]
                            ),
                            level=1,
                            re_export=True,
                        )
                        for stem, document in stems
                    ),
                ],
            ),
        )

    def _emit_document_modules(self) -> dict[PurePosixPath, str]:
        files: dict[PurePosixPath, str]
        generated_module: GeneratedModule

        match self._document_sibling_module:
            case None:
                modules = self._document_modules
                files = {
                    PurePosixPath(DOCUMENT_PACKAGE, "__init__.py"): _generated(
                        self._emit_document_reexports(modules)
                    )
                }
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
        """The generated package, as the content of each file keyed by its path relative to the package's directory, modules next to documents included: UTF-8, with LF line endings, whatever the platform."""
        schema_spellings = self._schema_spellings
        document_files = self._emit_document_modules()
        files: dict[PurePosixPath, str] = {
            PurePosixPath("__init__.py"): _generated(""),
            PurePosixPath(f"{SCALAR_MODULE}.py"): _generated(
                self._module_source(
                    emit_scalars(self._scalars, spellings=self._scalar_spellings),
                    generated_module=PackageModule(path=()),
                    preamble=(),
                )
            ),
            PurePosixPath(SCHEMA_PACKAGE, "__init__.py"): _generated(
                unparse(
                    emit_schema_package(self._schema, schema_spellings=schema_spellings)
                )
            ),
            PurePosixPath(
                SCHEMA_PACKAGE, f"{schema_spellings.enum_module}.py"
            ): _generated(
                self._module_source(
                    emit_enums(self._schema, schema_spellings=schema_spellings),
                    generated_module=PackageModule(path=(SCHEMA_PACKAGE,)),
                    preamble=(),
                )
            ),
            PurePosixPath(
                SCHEMA_PACKAGE, f"{schema_spellings.input_module}.py"
            ): _generated(
                self._module_source(
                    emit_inputs(
                        self._schema,
                        annotations=self._annotations,
                        schema_spellings=schema_spellings,
                        injector_names=self._injector_names,
                    ),
                    generated_module=PackageModule(path=(SCHEMA_PACKAGE,)),
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

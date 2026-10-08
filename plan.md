# `graphql-codegen` — a sans-io replacement for ariadne-codegen

## How to read this document

**Status: implemented, and `atoti-client` migrated.**
ariadne-codegen is gone from the repo: `uv run python -m project.graphql_codegen` generates `atoti/_graphql/client/` with this library.
[Implementation status](#implementation-status) records where the implementation departed from this plan; where the two disagree, that section wins.
This document is written to be picked up cold, with no prior conversation.

Confirmed by running it, not by reasoning: **347 types, 152 operations (102 queries, 50 mutations),
20 `@oneOf` inputs, 0 validation errors.** One design decision was overturned by contact with the
repo — see *Do not bump `graphql-core`* below.

**Everything asserted here with a number was measured**, against the real `activepivot` schema
(21 `.graphqls` files, 347 types) and the real 2009-line `operations.graphql` (152 operations used
by the SDK, 174 counting the Java server's test documents), on throwaway prototypes in a scratch
venv. Nothing was estimated. Where a claim is a judgement rather than a measurement it says so.
[Appendix A](#appendix-a--provenance-what-was-measured) consolidates every measurement in one place,
so nothing needs re-deriving.

**[Appendix B](#appendix-b--design-reversals-and-prototype-traps) is the most valuable section for
anyone continuing this work.** It records the seven places the design was wrong and what corrected
it, plus four concrete bugs hit while prototyping — including one
(`typing.is_typeddict` returning `False` for `typing_extensions.TypedDict`) that made an entire
transform a silent no-op while still passing its own error tests. These are traps, not history.

**No open questions remain.** Every design decision is settled and every flagged risk has been
checked against the repo. The one unverifiable-in-session item is the reason the library's own tests
carry the correctness guarantee — see [B.5](#b5--a-risk-that-turned-out-not-to-be-one).

Reading order if short on time: **Context** → **Decisions already made** → **Appendix B** →
**Milestones**. The **Design** sections are reference material for the milestone you are on.

**Constraints, fixed by the user and not up for re-litigation:**

|                        |                                                                        |
| ---------------------- | ---------------------------------------------------------------------- |
| Runtime dependencies   | `typing_extensions` **only** — no httpx, no pydantic, no graphql-core  |
| Generator dependencies | `graphql-core` (plus `hypothesis` for tests)                           |
| Python                 | `>=3.12`                                                               |
| Name                   | `graphql-codegen`, designed as a public library                        |
| Publishing             | **not now** — private, no PyPI, no PR                                  |
| Scope                  | **the library only**; migrating `atoti-client` is a separate follow-up |

## Implementation status

What is built, and every place it departs from the design sections below.

### Where operations live

- Operations are written in `.graphql` files next to the modules using them: 50 files in `atoti-client` and 3 in `atoti-client-ai`.
- Atoti's driver parses them into one executable document, since a fragment defined in one file is spread in others, while each definition keeps its own file for diagnostics.
- The library generates one package from one schema and one executable document: resolving dependencies between packages is not GraphQL's business, so it is not the library's.
- Atoti's plugins therefore have their operations generated into `atoti._graphql.client`, which costs nothing since `atoti-client-ai` pins `atoti-client` exactly, so they ship together, and they run their operations through Atoti's client anyway.
- The repository's `graphql.config.yml` has one project for Atoti's documents and its plugins', generated into that package, plus one for the server's test documents.
- It is YAML because graphql-config builds a project from its own entry only, inheriting nothing from the root: an anchor lets the schema glob be written once although both projects share it.
- `project.graphql_codegen` reads it with PyYAML and parses every file, keeping each file's source for diagnostics, then hands parsed ASTs to the library's `generate()`, and writes what it returns.
- It also strips the placeholder field Atoti's Java server declares in input types it extends at run time, which is Atoti's business rather than the library's.
- The `.graphql` files are excluded from the wheels: the generated modules inline the documents.

### Shape of the generated package

- `schema/` holds the enums and input types the documents reach (`schema/__init__.py` re-exports them), `fragment.py` every fragment's type, each after the fragments it spreads, since they may be its bases, in one module as fragments spread each other in no cycle, `operation/` one module per document (`operation/__init__.py` re-exports the operations only), and `scalar.py` one `type` alias per configured scalar, named like it.
- `runtime/` is a verbatim copy of `graphql_codegen.runtime`, so a generated package depends on nothing: before Python 3.15, whose standard library is the first with closed `TypedDict`s (PEP 728) and the builtin `sentinel()` (PEP 661), only `runtime/_compat.py`, which every generated module's `_te` is, and the definition of `_core`'s sentinel import `typing_extensions`; a test checks that no other import of it exists, and the bookshop client was run on Python 3.15.0b4 with nothing installed, and no project needs this library but to generate: only `project.graphql_codegen` imports it.
- The package imports its own modules relatively, so it works wherever it is written, and the scalar module imports what configures the scalars by the paths it is given: absolute, or starting with `..` to be relative to the package's directory.
- The runtime is maintained as ordinary modules of this library, tested there, and copied by `PackageEmitter.emit()`, rather than kept as source text in a string.
- GraphQL keeps operations, fragments and types in separate name spaces, and so does the package: no module re-exports names from two of them.
- An operation module exports `Foo`, `FooVariables` and `FooData`, named exactly like the GraphQL operation.
- `FooData` is the type of the response's `data` entry, the spec's response data, which pairs it with `FooVariables`, the request's `variables` entry, as Relay's `$data` and `$variables` do; `Output` would have reused a word the spec defines for something else, an output type.
- Every derived name is private and spelled `_<root>_<path>`: its root type's Python name, then response keys, and a union member's concrete type, verbatim and joined by `_`, such as `_GetTablesData_dataModel_database` or `_ExternalTableUpdatePerimeterCondition_leaf`.
- The root in the name is what makes a type checker message unambiguous, since ty and pyright print bare class names; Apollo's former TypeScript codegen spelled nested types the same way, `GetDogs_dogs`.
- Private, since the GraphQL way for code to name a shape is a fragment, whose type is public; the public names are those GraphQL gives, plus an operation's `FooVariables` and `FooData`.
- An operation constant is an `Operation[Literal["query"], FooVariables, FooData]`, or `Literal["mutation"]` or `Literal["subscription"]`, inferred from the `variables_type`, `data_type` and `type` it is constructed with, so constants need no annotation.
- `type` is the spec's `OperationType`, and so are its values.
- `Operation`'s type parameters and fields, and the keywords constructing it, follow the order things happen in: the operation's type first, as in its document, then its name and document, the variables going out, and the data coming back.
- An operation's description is the constant's docstring, and a variable's the docstring of its key; both are removed from the document sent to the server, since the spec says they must not affect execution, and a server whose parser predates them would reject the request.
- Calling an operation with its variables makes a request: `Foo({...})` is a `Request[Literal["query"], FooData]`, a value that runs nothing, checked where the variables are attached.
- A client runs a request: `client(Foo({...}))`, the transport's parameters after it, `client(Foo({...}), timeout=5.0)`, which `Client` captures with a `ParamSpec`, `Params`, from the transport's signature: parameters rather than options, since a transport may require one.
- A client takes its transport and injectors directly, `Client(transport, injectors=injectors({...}))`, with no context in between.
- A client prepares a request with its injectors, as a `requests.Session` prepares a `requests.Request` with its headers: a `PreparedRequest` is completed with its injected values, `bytes()` gives its body, and its `parse` reads the response, whether it runs one operation or several merged.
- A `Request` is not yet the spec's full request, since it lacks the injected values, which is why its completed form is named apart.
- The transport is what carries the request body to the service and its response body back: the spec leaves the transport mechanism to the implementation, and reserves execution for the service, so nothing on the client is an executor.
- A client runs several operations of one type as one merged operation, and returns the data of each in the shape they were passed: `client((Foo({...}), Bar({...})))` is a `tuple[FooData, BarData]`, typed by an overload per arity up to 6, and `client([Foo({...}) for _ in range(n)])` a `tuple[FooData, ...]`, the transport's arguments still following.
- 6 is where typeshed stops `asyncio.gather`, the closest analogue, which `test_clients_type_as_many_operations_as_asyncio_gather` keeps it in sync with, reading the stubs ty vendors; it is fixed rather than an option: a longer tuple of distinct operations is rare, a list of one operation type is typed at any length, and an option would make one runtime module generated rather than copied.
- One argument, whatever the shape, is what keeps the transport's arguments on every form: a `ParamSpec` needs `*args` for itself, so `client(*operations)` could not take them past the last overload.
- pyright rejects a tuple mixing a query and a mutation, while ty accepts it, so merging checks the operation types at run time too.
- Attaching the variables on the operation is what makes the check exact on every type checker: ty and mypy cannot check a signature taking an operation and its variables together, since they solve `Variables` from both.
- The operation type is a type parameter rather than a class hierarchy, so a batching client overloads on `Request[Literal["mutation"], Data]` and `Request[Literal["query"], Data]`, and branches on `bound.operation.type` at run time.
- An operation whose type is widened to `OperationType` matches neither overload, so it is rejected rather than mistyped.
- `Client` and `AsyncClient` accept only `Request[Literal["query", "mutation"], Data]`, and `SubscriptionClient` and `AsyncSubscriptionClient` only `Request[Literal["subscription"], Data]`, so a subscription never reaches a client expecting one response.
- Atoti's transport takes no parameters: its mutations are merged into one request, where a per call argument would be ill defined, so its batching client takes `validate_output` instead, which is execution policy rather than request data.

### Schema types, only those reached

- The generated code grows with the documents rather than with the schema, as ariadne-codegen does with `include_all_inputs = false` and `include_all_enums = false`.
- An input type is reached through a variable, and reaches the types of its fields in turn, a `@oneOf` input's members included; an injected field reaches nothing, since it is removed.
- An enum is reached like an input type, or when a field of its type is selected; the input type of a struct payload is reached when the payload is selected, since the payload has its shape.
- Atoti's documents reach 39 of its 41 enums and 143 of its 147 inputs; the six left out are used by no hand-written code.

### Test corpus: an independent bookshop

- One universe for the README, the tests, and the Hypothesis properties to come: `python/graphql-codegen/bookshop/`, with `schema.graphql`, one operation per file under `operations/`, `graphql.config.yml`, `scalars.py`, `generate.py` and `app.py`, the README's example, where the configuration and `generate.py` must generate the same client; the library's docstrings use it too, and nothing mentions Atoti any more.
- Every feature appears in it for a reason a reader recognises, so no case looks contrived: `Publication` and `Printed` make an interface chain, `SearchResult` a union, `BookLookup` a `@oneOf` input, `BookFilter` a recursive one whose `and`, `or` and `not` are Python keywords, `Money` and `DateTime` codec scalars, `ISBN` an identity scalar as a `NewType`, `idempotencyKey` an injected field, `AddressStruct` a struct, and `Membership` and `RestockInput` types no document reaches.
- It is a top-level package next to `tests_graphql_codegen`, so that its dotted paths read `bookshop.scalar.parse_money` in the README.
- Its generated client, `bookshop/client/`, is committed: the README shows one of its modules and the tests import it, and the command's end-to-end test fails, naming the command regenerating it, when it differs from what the command writes.
- Poka-yoke for the README: each code block follows a `<!-- file: … -->` marker and must equal that file, run or type checked by the tests, and a block without a marker fails unless it is a shell command.
- Tests needing a schema the bookshop cannot be keep a tiny one of their own: a struct interface breaking the convention, an injected field only a list or a recursion reaches, adversarial names, and the schema-level edge cases of `test_diagnostics`.
- Atoti's schema and documents are no longer tested here, and no Atoti test replaces them: the library's own tests are enough.

### Checks

- ty and pyright both pass on the whole package; pyright is run with `-p python/graphql-codegen`, whose `[tool.pyright]` fails on a suppression no longer needed, so that a test proving a call is a type error proves it for both, each line carrying `# ty: ignore[…]  # pyright: ignore[…]`.
- Branch coverage is 100%, measured by coverage.py alone, configured in the package's `pyproject.toml` and run from its directory; `if sys.version_info >= (3, 15):` branches are excluded, since each runs on its own versions only.
- The tests running the bookshop's client exercise its copy of the runtime, so coverage runs in parallel mode and `paths` merges the copy into `src/graphql_codegen/runtime/` when combining, the golden test keeping the two identical.
- Reaching 100% deleted code rather than testing it where it was dead: four unused AST builders, `load_document`, the resolver's diagnostics for what validation already rejects, now assertions, three copies of file parsing merged into `parse_documents`, and helpers only tests used, moved to `tests_graphql_codegen/_support.py`.
- No Hypothesis: names and the merge sigil have small, known edge cases that fixed tests cover, and merging, the one large space, is covered by merging every bookshop operation with every other of its type and validating each result, deterministic, instant, and needing no dependency.

### Guides

- Five guides in `python/graphql-codegen/docs/`, linked from the README: transports, merging, injected values and `@required`, errors and partial results, and migrating from ariadne-codegen.
- The poka-yoke of the README covers them: `test_docs.py` checks every block of the README and of `docs/*.md` against the file its marker names, relative to the document, like a link.
- Their examples are bookshop modules the tests run: `transports.py`, synchronous, over the standard library's `urllib` and run against a server on a local socket, its subscription parsing the events of the GraphQL over Server-Sent Events protocol by hand, and `async_transports.py`, asynchronous, over httpx2, which Atoti uses too, through httpx2's own `sse()` and driven by `httpx2.MockTransport`; `watch.py`, `cancellations.py`, `idempotency.py` and `partial.py`.
- httpx2 is therefore a test dependency, never a user's: the README's synchronous example keeps to the standard library's `urllib`, and only its asynchronous one, which the standard library has no API for, uses httpx2.
- `cancelOrder` is nullable, as a mutation meant to be merged should be: a failing non-null root field nulls the whole data, taking the other operations' results with it.
- The bookshop's schema is `schema.graphqls`, the repository's extension for schemas.

### CLI

- It reads graphql-config only, and its `extensions.pythonCodegen` holds the options: `codegen` would clash with GraphQL Code Generator's, which reads the same key, and anything far from `codegen` risks clashing with another tool, so the key names Python code generation, fixed, which spares a flag naming it and a check that GraphQL Code Generator's `generates` is not under ours; the options are keyed in camelCase like graphql-config's own keys: the schema and document globs stay written once, for the editor, graphql-eslint and code generation alike.
- No `pyproject.toml` table and no configuration on standard input: a four-line file also gives editors schema validation and completion, and anyone wanting no file at all calls `generate()`.
- JSON and TOML are read with the standard library; YAML needs PyYAML, behind a `graphql-codegen[yaml]` extra, so that no user gets a dependency they do not need, and a YAML configuration without it fails naming the extra.
- Globs follow `pathlib`, `*` and `**`, and each must match a file, so that graphql-config's micromatch-only syntax, braces and `!` negation, which `pathlib` reads as matching nothing, fails rather than being silently left out, with a message saying so, `pathlib` itself rejecting an absolute glob; a JavaScript or TypeScript configuration fails with a clear message too.
- `graphql-codegen CONFIG`: the path is mandatory, hence positional, which spares mirroring graphql-config's list of file names to discover and says which directory the globs are resolved against; there is no flag per option.
- Arguments are parsed with `argparse`, filled into a typed `Namespace` subclass, so that everything past parsing is typed: one positional and one flag need no dependency.
- It lives in `graphql_codegen._cli`, runs as the `graphql-codegen` script and as `python -m graphql_codegen`, and exits with 0, 1 on any failure, printed as the same diagnostics as a GraphQL error, or 2 on invalid arguments.
- Without `--project`, every project with the extension is generated, as Atoti's driver does; a file without `projects` is the single project graphql-config names `default`.
- A key the extension does not know fails naming the ones it does, since a typo such as `injectorName` would otherwise be ignored silently.
- The settings map onto `Config`, the frozen dataclass `generate()` takes besides the document and the schema, so that they are declared, defaulted and checked once: singular, unlike `Options`, its earlier name.
- The package replaces its directory, so that a deleted operation's module does not linger, rewriting only the files whose content changed so that whatever watches the directory sees only what did, but only a directory that is empty or holds a package generated before, recognised by `runtime/_core.py`: a mistaken `directory` fails instead of losing files.
- No `--check`: a generated package is left to a build script, as Atoti does, generating its client with the Java jars that switching branches already rebuilds, while the committed test corpus keeps showing what a generator change does to Atoti's documents.

### Public API

- `graphql_codegen.generate(schema=, document=, ...)` takes a built `GraphQLSchema` and a parsed executable document, the pair graphql-core's `validate(schema, document)` takes, and returns every file's source by path: it reads and writes no file.
- graphql-core has no type telling a type system document from an executable one, but `GraphQLSchema` tells the schema apart, and a schema from introspection (`build_client_schema()`) plugs in with no print and parse round trip.
- The client directive is added with `extend_schema()`, 5 ms on Atoti's schema, keeping each type's source for diagnostics; the schema is checked with `validate_schema()`, whose errors are located, where `assert_valid_schema()` raises a bare `TypeError`.
- `scalars` configures each custom scalar with dotted paths, absolute or starting with `..` to be relative to the package's directory (a single dot would name the generated package's own modules), `Mapping[str, IdentityScalar | CodecScalar]`: an `IdentityScalar` has a `type` alone, for a value that stays as JSON decodes it, and a `CodecScalar` has a `type`, a `parse` and a `serialize`, for a Python value differing from its JSON one; a bare name is a builtin.
- Strings rather than Python objects, so that generating imports nothing, a configuration file can hold them, and Atoti's driver no longer registers a stub `atoti` package to import its scalar modules without running `atoti.__init__`, which imports the client being generated.
- The two closed `TypedDict`s make ty and pyright reject a `parse` without `serialize`, a misspelled key and a missing `type`; the generator checks the same at run time, and that each path is made of identifiers, since a configuration file is not type checked.
- A path naming nothing only fails when the generated package is type checked or imported, the price of importing nothing.
- An identity scalar costs nothing at run time, where an identity codec would convert every value: validating a scalar is the server's job, and a `NewType` with a validating constructor gives the client "a URL, not any string" statically.
- `struct_interface_name` is all a struct needs: the generator checks that the interface has a single field, of a custom scalar type, and that every type implementing it is named after an input type followed by the interface's name.
- `required_directive_name` enables the client directive asserting that a nullable field is not null, disabled by default; when set, it is declared for validation and stripped from the documents sent.
- `injector_names` names the injectors, each supplying the variables and input fields of its name, once for every operation: `*_names` like the other options' `*_name`, and naming what the generated code creates, one injector per name.
- Generated operation constants are not annotated `Final`, which made pyright reject `operation/__init__.py` re-exporting each from the module named like it; the inferred type is exact either way, and Atoti imports `operation` to call `operation.Foo`, which both checkers resolve to the constant.
- The documents carry no `#graphql` marker, which cost bytes on every request and highlighted nothing.

### Subscriptions

- A subscription returns the spec's response stream: a `SubscriptionClient` returns a generator of response data, each response parsed like a query's, and one carrying errors raises out of the stream.
- Its transport is a `SubscriptionTransport`, taking the request body like any transport but yielding one body per response.
- That transport must be a generator, since closing the response stream closes it, and its cleanup is where it unsubscribes; an error in a response closes it too.
- Server-Sent Events, `graphql-transport-ws` and multipart HTTP are all subscription transports, and this library ships none, as it ships no HTTP transport either.
- Atoti's schema declares no subscription, so only the library's own tests run one.

### Merging

- "Merging" rather than "batching", which in GraphQL means sending separate operations in one HTTP request, or a server coalescing its data fetches: Atoti's `OperationBatcher` batches, collecting mutations and scheduling them, and the library merges.
- The generator prints every query and mutation document as a merge template, with `§` wherever the operation's index goes: after each root field's response key, as its alias, and after each variable's name.
- The runtime then merges by string replacement, with `_<index>`, and slices the template only at blank lines and at the header's parentheses, which hold since strings print on one line, block strings included.
- A lone operation drops every `§`, leaving each root field aliased as itself, `book: book`, which GraphQL executes as if it were not aliased: matching the redundant aliases to drop them took a regular expression with a lookbehind, sparing a variable named like its type, `$ID§: ID`, for a document that only reads differently.
- The template is written from the AST, so no printer detail can make it wrong, and no generation-time check remains; a literal `§` in a string is escaped as `\u00a7`, so every `§` left is a sigil.
- The generator inlines, as inline fragments, the two things a template cannot hold: a fragment spread at the root, whose fields need aliasing, and a fragment using variables, which each operation suffixes differently; the server executes the result the same.
- An operation with directives of its own gets no sigil, since they would apply to the whole merge, and neither does a subscription, which must select a single root field: the runtime refuses to merge a document without one, and it still runs alone.
- `build_merged_body`, `split_merged_data` and `split_merged_errors` are the low level functions, untyped, that `Client` and Atoti's `OperationBatcher` both build on; the batcher keeps its scheduling, `ContextVar` and futures, and submits operations with the variables resolved at submission.
- A lone operation is sent under its own name, so the server knows it; a merged one is named `MergedOperation`, as Atoti's old merger named it.
- Checked against that AST merger, on every run of three consecutive Atoti operations: the 43 mutation merges are the same documents, and the 84 query merges are valid where the old merger made 73 invalid, since it suffixed variables only in root arguments.

### Names

- Every name the generator invents -- helper module aliases such as `_te` and `_builtins`, nested types, functional bases, schema submodules -- is a symbol, spelled once its module is complete: its preferred name if nothing else in the module uses it, else the first free `_<n>` suffix.
- Clashes are therefore impossible rather than detected, for any name GraphQL allows, `_`-prefixed ones included; no input rule remains, and the shadowing check is gone.
- Everything a module does not declare is referenced through a helper alias, builtins included (`_builtins.str`), so a public name can never shadow one the module uses.
- A key a class body cannot declare -- `__typename`, a keyword, a name the module references unqualified -- goes into a functional base for outputs, or makes the whole type functional for inputs, whose references to other inputs are then deferred through `type` aliases.
- A public name that is a Python keyword gets PEP 8's trailing `_`; two documents whose modules would differ only by case are an error, since `Foo` and `foo` are one file on macOS and Windows.
- Fragments are named in PascalCase in the documents, so their types are named exactly like them.

### Output types, per concrete type

- A selection on an abstract type is a union with one member per concrete type, gathering the fields of every condition that type satisfies, so conditions nested in conditions need no case of their own.
- Fields selected several times under one key are merged, as GraphQL merges them.
- A fragment is inherited only when its type is a class and every key it shares with the node is the very same one; otherwise it is expanded in place.
- A selection that `@skip` or `@include` may leave out is `NotRequired`, since it is then absent from the response, not null; one they always leave out is not in the type at all.
- A conditional fragment spread is expanded rather than inherited, since inheriting it would declare its keys present, and a key is required as soon as one of the selections merged under it is unconditional.
- A `__typename` under `@skip` or `@include` does not count as the discriminator, so `load_document` still adds one.

### Runtime work, derived from the types by reflection

- The generator emits no conversion code: an operation module holds its types and one constant, `Operation(type=, name=, document=, variables_type=, data_type=)`.
- `graphql_codegen.runtime._reflection` builds a parser from `data_type` and a serializer from `variables_type`, once per type, and caches them; a type needing no work builds to `None`, so such a response reaches the caller untouched.
- The build recurses over the grammar of types: TypedDicts and their bases, lists and `Sequence`, `X | None`, PEP 695 aliases, unions told apart by their `__typename` `Literal`, and `@oneOf` unions told apart by their single key; a union it cannot tell apart is rejected when built.
- Nesting, lists of lists, `@required` under a list and recursive inputs are therefore not special cases, and whether a type needs work is settled as a least fixed point over everything it reaches, since inputs reach each other in cycles.
- What a type cannot say rides on it as `Annotated` metadata: `Annotated[T, REQUIRED]` on a `@required` field, and a `Codec(parse=, serialize=)` on a custom scalar, in the generated scalar module, such as `type SecondsSinceEpoch = Annotated[datetime, Codec(...)]`.
- A response is converted in place, since the client owns it; variables are copied, since the caller may reuse them.
- `RequiredFieldError.path` names the exact value, list indices included, completed on the way up by each enclosing converter, at no cost unless raised.
- A struct payload (`FooStruct.value`) is typed as `input Foo`, so it is walked like any other field, enabled by `struct_interface_name="Struct"`.
- Injected variables are removed from the generated types, and so are injected fields of input types.
- `Operation.injections` maps each injector's name to the path of every object receiving its value: `()` for the variables themselves, `("input",)` for the `input` variable, deeper for a nested input, since the recommended mutation design takes a single `input` nested as much as possible.
- The key written is always the injector's name, and each injector is called once per request, so every target receives the same value; an object on a path that the caller left out or set to null receives nothing.
- An injector returns `OMITTED`, a `typing_extensions.Sentinel`, to leave its value out, and `None` to send `null`: the spec's "Nullable vs. Optional" tells omission and `null` apart, and Atoti's server rejects an explicit `null` for a transaction ID.
- A static path can say neither "every element of this list" nor a recursive input's unbounded depth, so an injected field reached that way fails generation, where it used to be removed from its input type and silently never injected.
- An injector supplies one value for all its positions, so every position the documents inject into must have the same type, nullability aside, or generation fails naming one position per type.
- The injector's type is the strictest of its positions, non-null wherever any is, since it cannot tell which position it is called for, and a non-null value suits a nullable position too; a schema mixing `ID` and `ID!` stays injectable, at the cost of `OMITTED` for that injector.
- The generated `injector.py` types each injector by that type in `InjectorFunctions`, allowing `None` and `OMITTED` only when it is nullable, as the spec allows for any input.
- `InjectorFunctions` is passed as a dict rather than as keyword arguments: any GraphQL name is a key, a keyword included, and a misspelled one fails with both ty and pyright, while ty misses a misspelled keyword argument and pyright rejects a keyword argument mixed with an unpacked dict.
- Its `injectors()` returns a `runtime.Injectors`, not a mapping, and a client takes nothing else, so injectors cannot skip the type check.
- `runtime.Injectors` serializes each injected value as its declared type says, read off `InjectorFunctions` at run time, so a custom scalar's codec runs: the value is injected after the caller's variables are serialized, and used to go out as returned.
- The module imports `OMITTED` by name, since pyright only takes a sentinel as a type when it is spelled as a bare name, rejecting `_runtime.OMITTED`.
- The mapping is excluded from `Operation`'s hash, which it would otherwise make unhashable.

### Output types

- `__typename` is added to every selection set on an abstract type, by `load_document`, so that every union is discriminated.
- Every possible type gets a member, including those no condition selects on, which carry only the common fields: `GetTablePartitioning` can return an `ExternalTable`, and `GetRoleMappingItems` a `KerberosSecurity`.
- `__typename` is `ReadOnly`, which is what lets a branch narrow the `__typename` of a fragment on an interface that it spreads.
- A fragment spread on a broader type than the one selected, such as an interface fragment inside `... on Implementation`, always applies, so it is a base class rather than a branch.
- A `@oneOf` member is non-null: the schema declares every member nullable, but the member that is set cannot be null.
- An introspection enum, such as `__TypeKind`, is a `Literal` spelled where it is selected, since it is not a type of the schema and so not in its package.
- A fragment's description, when it has one, is its type's docstring instead of the description of the type it is on.

### Removed

- The `@mixin` compatibility declaration, the unused `_config.py` and the empty `plugins/` package.
- The workarounds for ariadne-codegen's `interface` chain bug: the repeated per-type spreads in `GetRoleMappingItem(s)` and the explicit `__typename` selections.
- The emitted transforms, variables serializers and per input codec functions, which described a second time what the types already describe: reflection replaced them, as this plan had decided.
- The shadowing check and every naming rule on the inputs: allocation makes clashes impossible instead.
- The curried `client(Foo)(variables)` call, replaced by binding: `client(Foo(variables))`.
- The `Query` and `Mutation` classes and their bound counterparts, replaced by the operation type as a type parameter of `Operation` and `Request`.
- Atoti's `_merge_documents.py`, `_merge_variables.py`, `_naming.py` and `_unmerge_output.py`, and their test, replaced by the runtime's merging.

### Errors

- A response carrying errors raises a `RequestError` when it has no `data` entry, the spec's request error result, and a `ExecutionError` when it has one; both are `ResponseError`s carrying the errors and the response's `extensions`.
- An operation's `ExecutionError` holds its partial data parsed like data would have been, codecs included, so a caller keeping what succeeded reads the same shapes; `parse_response` alone leaves it as received.
- The server nulls a `@required` field that raised, since the field is nullable for it, so the parser propagates that null to the nearest nullable parent, up to the data itself, as the server does for a non-null field; a `@required` null in a response without errors still raises `RequiredFieldError`.
- The partial parser is a second cached converter per type, built only once a response with errors needs it, so a successful response pays nothing for it.
- A merge raises an `ExceptionGroup` of one `ExecutionError` per operation its errors concern, each with that operation's own data and paths and a note naming its index; an error without a path concerns them all, and a merge whose data is null raises its error unsplit, since no operation has any data left; `except*` catches both.
- Atoti's `OperationBatcher` still raises the merge's error as received: delivering the outputs of the mutations it spared would feed futures that nothing reads once the batch has raised.

### Diagnostics

- A diagnostic on a node is located by the node's first token, not by `graphql.get_location()` as designed below: in `graphql-core` 3.2.12, as in 3.2.6, `Source.get_location()` is one line early at the start of a line, where every definition starts.

### `graphql-core`

- Bumped to 3.2.12, the latest stable release, now that ariadne-codegen is gone: its parser accepts descriptions on operations, fragments and variables, which 3.2.6 rejected.
- 3.2.12 declares `@oneOf` itself, so one-of-ness is read off `GraphQLInputObjectType.is_one_of`, and the declaration this library made and the AST reading of `get_one_of_input_names` are gone.
- It is a dependency of the generator only: no generated package and no Atoti package imports it.

### Known limits

- A lone anonymous operation is ignored, since its constant would need a name.

## Context

`atoti-python-sdk` generates its GraphQL client with `ariadne-codegen==0.16.0`, driven from
`python/project/src/project/ariadne_codegen/_generate_client.py`. The stock generator does not do
what this repo needs, so it is bent into shape by a ~430-line `ast`-rewriting plugin
(`plugin.py`) plus a monkeypatch of an ariadne internal
(`add_support_for_struct_types.py` replaces `ResultTypesGenerator._parse_type_definition`).

That arrangement has three standing costs:

1. **It fights the tool.** Eight plugin hooks rewrite the emitted AST after the fact — mutations are
   gutted and re-assembled statement by statement, `httpx` is regex-replaced with `httpx2` in
   copied template files, and three `assert`s exist purely as tripwires for ariadne behaviour
   changes (`"ariadne-codegen's behavior has changed, this function can be removed."`).
2. **Its limitations leak into the schema-facing source.** `operations.graphql` carries workarounds
   for [ariadne-codegen#254](https://github.com/mirumee/ariadne-codegen/issues/254) — bare
   `__typename` selections and fragment spreads repeated once per concrete type, because interface
   chains (`SsoSecurityWithRoleMapping implements SsoSecurity`) generate wrong unions.
3. **Non-null overrides are verbose and unchecked.** The 47-character string
   `@mixin(from: ".mixins", import: "NonNullable")` appears **221 times**; it strips `Optional[...]`
   from the annotation but never validates, so a real null silently becomes `None`.

We want a generator whose built-in feature set is what we actually need: sans-io output, TypedDicts,
custom scalars, first-class struct support, a real nullability directive, and batching/async as
plugins — with no runtime dependencies.

**Scope of this plan: the library only.** ariadne-codegen stays wired up and running. The new
package is proven by generating against the real schema and the real 2009-line
`operations.graphql`, under golden-file tests. Migrating `atoti-client`'s ~60 modules from
attribute access to subscripts is a separate follow-up, sized at the end.

No PR, no publishing.

---

## Decisions already made

| Decision              | Choice                                                                                                                                                                                  |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Name                  | `graphql-codegen`, designed as a public library, shipped private for now                                                                                                                |
| Where it lives        | `atoti-python-sdk/python/graphql-codegen/` — a uv **workspace member** from day one, private, a dev dependency of `atoti-root` exactly as `test-utils` and `project` are                |
| Dependencies          | runtime: `typing_extensions` only. Generator: `graphql-core`. Python `>=3.12`. One flat list, **no extras** — the runtime/generator split is proven by an import test, not by packaging |
| Nullability directive | `@required` (Relay's name and model), THROW-only, no `action` argument                                                                                                                  |
| Keys                  | exactly as declared in the schema/operations — no case conversion, no aliases                                                                                                           |
| TypedDict syntax      | class-based, inheriting a functional base for `__typename`; fragments compose by inheritance                                                                                            |
| Validation            | **none at runtime** — static typing only; servers are assumed well-behaved                                                                                                              |
| Plugins               | submodules selected by config — not separate distributions                                                                                                                              |
| Batching              | core: pure `merge`/`split` + a typed deferral-free `batch()`. Ambient scope stays in ActivePivot                                                                                        |
| Async                 | a plugin, not a config flag                                                                                                                                                             |

### Why `@required` and not `@nonnull` / `@semanticNonNull`

Apollo Kotlin **deprecated** `@nonnull` in favour of schema-level `@semanticNonNull` +
`@catch`. Those solve a different problem: "the schema is imprecise; this is null only on error".
Classifying all 221 sites by _why_ they are non-null:

| Reason                                        | Count     | Example                                           |
| --------------------------------------------- | --------- | ------------------------------------------------- |
| Lookup where the caller knows the name exists | 116 (52%) | `cube(name:)`, `hierarchy(name:)`, `level(name:)` |
| Auth / config / plugin gated                  | 24 (11%)  | `security`, `sso`, `chat`, `autoExplain`          |
| Genuinely never null in practice              | 81 (37%)  | `Query.dataModel(transactionId:)`                 |

63% are **caller assertions about one query**, not schema imprecision — which is exactly Relay's
`@required`, and exactly what `@semanticNonNull` must not be used for. Marking `DataModel.cube`
semantically-non-null at schema level would be a lie that breaks the first caller who wants an
existence check.

Relay's mandatory `action:` argument is dropped: `NONE`/`LOG` bubble the null up to the parent,
which fights the point here (the SDK wants the whole `dataModel.cube.dimension.hierarchy` chain
non-null), and an emitted `is None` check gives THROW for ~free. Absent-means-THROW leaves room to add `action`
later without breaking the 221 call sites.

### Batching with no `graphql-core` at runtime

Yes — and it removes `graphql-core` from the runtime **entirely**.

Today `_batching/_merge_documents.py` (161 lines) does AST surgery at runtime to alias each root
field and suffix each variable. But the generator knows every operation statically, so all of that
can be precomputed at build time and merging becomes string concatenation:

```python
# emitted per operation
_CREATE_MEASURE: Final = BatchableOperation(
    variable_definitions="$input_§: CreateMeasureInput!",
    selections="createMeasure_§: createMeasure(input: $input_§) { measure { name } }",
    fragments=(_MEASURE_IDENTIFIER_FRAGMENT,),
    variable_names=("input",),
)
```

Merging N operations is then: `str.replace("§", str(i))` per operation, join the selections, join
the variable definitions, dedup fragments by name, and suffix each variable key. Un-merging already
works this way — `_unmerge_output.py` is 18 lines of string handling with no AST.

Use a reserved single-character token and `str.replace`, **not** `str.format`, so GraphQL's braces
need no escaping.

Result: `graphql-core` is generator-only. The runtime that ships inside `atoti-client` needs only
`typing_extensions`.

---

## Package layout

**Tactical decision: build it as a workspace member from day one**, at
`atoti-python-sdk/python/graphql-codegen/`, private, pulling `graphql-core` itself. It is a dev
dependency of the root `atoti-root` package exactly as `test-utils` and `project` are. Developing it
inside the uv workspace means the whole system — `uv sync`, `uv run ty check`, `uv run pytest`,
pylint, ruff, coverage, CI — exercises it from the first commit, rather than discovering integration
problems after the library is "done".

It is **one flat dependency list, no extras.** An earlier draft split `graphql-core` into a
`[project.optional-dependencies] codegen` extra so the runtime half could install without it. That
is premature: nothing consumes the runtime half as a separate distribution yet, and the split is
better **verified than declared** — a test that imports `graphql_codegen.runtime` with
`graphql-core` blocked from `sys.modules` proves the boundary holds, where an extra only asserts it.
(The same trick already worked in A.1's zero-dependency probe, which imported the client with
`typing_extensions` blocked.) Add the extra when someone actually installs the runtime alone.

```
atoti-python-sdk/python/graphql-codegen/
  pyproject.toml
  src/graphql_codegen/
    py.typed
    __init__.py
    runtime/                # ← the half that would ship to end users. typing_extensions only.
      _core.py              # LAYER 1, pure: build_request / parse_response / merge / split
      _request.py           # GraphQLRequest, Multipart / FilePart aliases
      _executor.py          # Executor / AsyncExecutor Protocols (LAYER 2/3 seam)
      _errors.py            # GraphQLFormattedError (spec §7.1.2, re-declared), GraphQLError,
                            # GraphQLMultiError, HttpError, ProtocolError,
                            # RequiredFieldError
      _client.py            # the thin sync binding over layer 1
      batching/             # BatchableOperation + the typed batch() overloads — no graphql-core
    codegen/                # ← dev-only. needs graphql-core.
      __main__.py
      _config.py            # the Config dataclasses the user's graphql_codegen_config.py imports
      _schema.py            # load + merge + strip, declare client directives
      _document.py          # parse + validate operations, strip client directives for the wire
      _ir.py                # schema/document → intermediate representation
      _naming.py
      _emit/                # _types.py _client.py _transforms.py
      plugins/
        _plugin.py          # Plugin protocol
        batching.py
        async_.py
        struct.py
  tests_graphql_codegen/
    __init__.py
    __resources__/<case>/   # golden files, per repo's snapshot idiom
```

`pyproject.toml` mirrors `python/project/pyproject.toml` line for line:

```toml
[build-system]
build-backend = "uv_build"
requires = [
    # Keep in sync with `../../../pyproject.toml`'s `tool.uv.required-version`.
    "uv-build==0.11.16",
]

[project]
classifiers = ["Private :: Do not Upload"]
dependencies = [
    "graphql-core>=3.2.6",
    "hypothesis>=0",
    "typing-extensions>=0",
]
name = "graphql-codegen"
requires-python = ">=3.12"
version = "0.0.0"

[tool.uv.build-backend]
module-name = "graphql_codegen"
```

### Registration — what `python/graphql-codegen/` costs elsewhere

|                                                                     |                                                                                                              |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `[tool.uv.workspace]`                                               | **nothing** — `members = ["python/*"]` picks it up automatically                                             |
| Root `[project].dependencies`                                       | add `"graphql-codegen"`, alphabetically                                                                      |
| Root `[tool.uv.sources]`                                            | add `graphql-codegen = { workspace = true }`                                                                 |
| `javascript/pyproject-toml/src/parseWorkspacePackageDirectories.ts` | both entries above must use the exact one-line form — it parses them with a regex                            |
| `python/pylint-plugins-atoti/.../undeclared_dependency.py`          | **nothing** — it normalises `-` to `_`, so `graphql-codegen` already matches module `graphql_codegen` |
| `uv lock`                                                           | run it                                                                                                       |

**Starting private buys two things beyond "not published yet."**
`python/project/tests_project/conftest.py:22` filters the requirement fixtures with
`if is_published(package_name)`, so a `Private :: Do not Upload` package is **excluded from
`test_synced_requirements` and `test_minimum_version_installed`**. That means the new package can
pin `graphql-core>=3.2.12` freely while the rest of the workspace catches up, and its dependency
specifiers are not forced to match the published packages'. Flipping it public later is where those
tests start applying — a deliberate, separate step.

### Do **not** bump `graphql-core` — declare `@oneOf` instead

An earlier draft made milestone 1 bump `graphql-core` to `>=3.2.12` for `@oneOf`, and called
"confirm `ariadne-codegen==0.16.0` still resolves" the check that de-risked it. **Resolving was the
wrong test, and the bump is wrong.** Measured, not assumed:

- `uv lock` against `graphql-core==3.2.12` resolves cleanly and `uv sync` installs — so the
  dependency check passes.
- **ariadne-codegen 0.16.0 then crashes at run time**, exit 1, on the very first operation:
  `TypeError: Redefinition of reserved type 'String'`. Its `_get_field_from_schema` synthesises
  `GraphQLScalarType(name="String")` for `__typename`, and graphql-core 3.2.12 added a guard
  (`GraphQLNamedType.__new__`) forbidding redefinition of reserved types.
- That breaks the repo outright: no generated client means `import atoti` fails, so the whole test
  suite goes down.

ariadne-codegen **0.19.0 fixes it** (it uses the `GraphQLString` singleton), but upgrading a
generator this project *deletes* at the end is wasted risk — `plugin.py` monkeypatches the private
`ResultTypesGenerator._parse_type_definition` and carries three tripwire `assert`s on ariadne
behaviour.

**So the pin stays at `graphql-core==3.2.6`, and `@oneOf` is handled by this library instead**, the
same way `@required` and `@mixin` already are:

1. `codegen/_schema.py` declares `directive @oneOf on INPUT_OBJECT` — but only when the installed
   graphql-core does not already declare it, checked against `specified_directives`.
2. One-of-ness is always read off the **AST** (`get_one_of_input_names`), never off
   `GraphQLInputObjectType.is_one_of`.

Verified both directions: on 3.2.6 the schema builds and the AST reading finds the **20** `@oneOf`
inputs; on 3.2.12 the AST reading and graphql-core's built-in `is_one_of` produce **identical**
sets. A version-gated test asserts that agreement so the shim cannot silently drift.

This is strictly better than the bump for a library meant to be published: `graphql-codegen` now
works on any graphql-core 3.2.x rather than requiring the newest, and `atoti-client`'s
`graphql-core>=3.2.6` line never has to move.

## Design

### Genuinely sans-io: a pure core, with sync/async as thin bindings

**An executor is not sans-io.** In h11 or hyper-h2 the library never calls anything — you feed it
bytes, it returns bytes and events, and one core serves asyncio, trio, threads or a replay harness
because it has no concept of waiting. Taking an `Executor` is _dependency injection of IO_: the
library still owns the control flow, which is exactly why a separate async variant is needed. The
`async` flag is the symptom.

The honest structure is the one h11 uses — a pure core, with bindings layered on:

**Layer 1 — the core. Pure functions, no IO, no async, no executor.**

```python
build_request(operation, variables)     -> GraphQLRequest     # data in, data out
parse_response(operation, raw)          -> Output             # data in, data out
merge_requests([r1, r2, ...])           -> GraphQLRequest     # batching
split_response(raw, count)              -> [raw1, raw2, ...]
```

Nothing here waits, sleeps, or calls out. It works under any IO model — including ones we never
anticipate — and it is testable with zero transport, which matters given the Hypothesis corpus.

**The boundary is bytes, not parsed JSON.** Type safety is unaffected — it lives in the signature,
not in what crosses it — so this is purely about the seam:

```python
build_request(operation, variables)  -> bytes | Multipart
parse_response(operation, raw)       -> Output          # raw: bytes
```

This is what makes it h11-shaped rather than merely dependency-injected: we own neither transport nor
encoding. It also simplifies the executor to about the thinnest thing possible —
`(body: bytes, *, headers) -> bytes`.

Measured gain, using the real operations: because the document is a constant, the envelope is
serialised **once** and only the variables vary per call.

The _envelope_ is the `{"operationName": …, "query": …, "variables": …}` wrapper the spec requires.
Two of its three keys are known at codegen time, so the generator emits the JSON around the
variables already encoded and already escaped:

```python
# emitted once, per operation
_GET_X_PREFIX: Final = b'{"operationName":"GetX","query":"query GetX($cubeName: String!) {\\n  dataModel {\\n    cube(name: $cubeName) ...","variables":'
_GET_X_SUFFIX: Final = b"}"

# per call
body = _GET_X_PREFIX + json.dumps(variables).encode() + _GET_X_SUFFIX
```

What this removes is not the `json.dumps` call but the **document's string escaping** — every `"`
and every newline in a 751-byte query, re-escaped on every request today. Three notes:

- It is only possible because the boundary is `bytes`. A `dict`-shaped executor hands the document
  to the caller's `json=` and re-serialises it every call, so this is a concrete payoff of the seam
  rather than a free-standing optimisation.
- Variables must serialise with `json.dumps`' default separators for the concatenation to be valid
  JSON; the suffix is a bare `}`.
- **Batching does not get it.** A merged document is built at runtime from the merge templates, so
  it is serialised once per batch instead of once per operation — still amortised over N operations,
  just not ahead of time.

|                                       | median op (270 B) | largest op (751 B) |
| ------------------------------------- | ----------------- | ------------------ |
| dict envelope, re-serialised per call | 4.02 µs           | 5.89 µs            |
| pre-serialised envelope               | **2.39 µs**       | **2.44 µs**        |

1.7–2.4x, but the shape matters more than the number: request building becomes **flat in document
size**, where a dict-level API pushes the whole document through `json.dumps` every call. In absolute
terms 2–3 µs is noise against a round trip — take this for the seam, not the speed.

Three costs, none fatal:

- **Caller ceremony.** `content=` rather than `json=`, and the caller sets `Content-Type`. Easy to get
  wrong, so the docs must show it.
- **Non-JSON responses.** An HTML error page or proxy failure now arrives as bytes we must reject
  cleanly; today `.json()` raising is the caller's problem.
- **Uploads do not fit the bytes boundary.** Multipart must stay a _structure_ so `httpx`/`aiohttp`
  can stream files rather than us buffering them — hence the `bytes | Multipart` union. **There is no
  stdlib type for this.** `email.message.EmailMessage` with `email.policy.HTTP` builds a correct
  `Content-Disposition: form-data` part, but `BytesGenerator.flatten()` materialises it all into a
  buffer (and base64-encodes by default), defeating the point; `cgi` is parse-only and removed in
  3.13. The de-facto shape is the convention `httpx` and `requests` share, expressible in pure stdlib
  generics and requiring **zero adaptation** at the call site:

  ```python
  FilePart: TypeAlias = tuple[
      str | None, IO[bytes] | bytes, str | None
  ]  # filename, content, type
  Multipart: TypeAlias = tuple[
      Mapping[str, str], Mapping[str, FilePart]
  ]  # (form fields, files)
  ```

  Form fields carry `operations` and `map` per the multipart spec; files are the numbered parts. The
  caller writes `post(url, data=fields, files=files)`, `IO[bytes]` stays lazy so uploads stream, and
  being a type alias it adds no class to the runtime.

No gain on the response side: we would `json.loads` where the caller currently does — identical work.
Single-pass parse-and-transform via `json.loads(object_hook=...)` does not help either, since
`object_hook` exposes no path and our transforms are path-driven.

**Layer 2/3 — sync and async bindings.** Each generated method is one line over the core:

```python
def get_x(self, variables, /, *, options=None):
    return parse_response(
        _GET_X, self._execute(build_request(_GET_X, variables), options)
    )
```

The async binding is the same with `await`. The "flag" stops being a property of the library and
becomes _which thin wrapper you import_ — and a consumer with an IO model we did not think of skips
both and uses layer 1 directly.

**What this forces, usefully: the impure parts move out of the core.** Injected variables read
`ContextVar`s, and the batch scope is also a `ContextVar` — ambient state is not IO, but it is not
pure either. Both belong in the binding: `build_request` takes _final_ variables, and the binding is
what merges injected ones and decides whether to enqueue. That is a cleaner split than the current
plan has.

**Batching lands in the core, which is a real gain.** `merge_requests` and `split_response` are pure
functions over data — exactly the precomputed-template design — so batching works under any IO model
rather than only the two bindings we ship.

Cost: close to nothing. The core functions are what the generator already emits; the bindings are one
line per operation. What changes is mostly the framing — and the framing was wrong.### No case conversion, and no runtime validation

Two decisions that settle most of the type design:

**Keys are exactly the names declared in the schema and operations.** No snake_case conversion, no
aliases. This reverses `plugin.py:355`'s `_allow_snake_case`, and it is a simplicity call, not a
performance one — measured, dropping aliases saves only ~3% (473 → 460 µs on a large payload),
because the traversal dominates, not alias resolution. What it removes is a whole category of
generator logic and a class of naming bugs.

### TypedDict syntax: class-based, with a functional base for reserved names

Python mangles any class-body identifier with two leading underscores, annotations included, so
`class X(TypedDict): __typename: str` silently becomes `_X__typename` and a real payload is rejected
with both `missing ('_X__typename',)` and `extra_forbidden ('__typename',)`. That rules out _naive_
class syntax — but not class syntax.

**The fix is a one-line functional base**, which keeps everything class syntax buys:

```python
_Typename = TypedDict("_Typename", {"__typename": Required[Literal["InMemoryTable"]]})


class TableIdFragment(_Typename, closed=True):
    name: Required[str]


class ColumnSelection(TableIdFragment, closed=True):  # fragment spread = inheritance
    extra: Required[int]


class BothSelection(TableIdFragment, JoinIdFragment, closed=True):  # several spreads
    other: NotRequired[str]
```

Verified end-to-end: `__typename` stays unmangled; `closed=True` survives inheritance
(`__closed__` is `True` on the subclass and an extra key is still `extra_forbidden`); single and
multiple inheritance both merge keys correctly; and `ty` sees inherited keys, reporting missing
required keys from the base, wrong value types, and unknown keys.

Use the functional form only where a name cannot be an identifier — `__typename`, or a field named
`from`/`class` should the schema ever grow one — and inherit from it. All 327 current field names are
valid identifiers, so in practice this is `__typename` alone.

Where the functional form is used, the string passed as the first argument must match the variable it
is assigned to, or `ty` raises `mismatched-type-name`.

### Emit `__typename` as a `Literal`, not `str`

This is what makes every output union narrowable. Verified in `ty`:

```python
def role(sso: LdapSso | OidcSso) -> str:
    if sso["__typename"] == "LdapSecurity":
        return sso["ldapRole"]  # narrowed
    return sso["oidcRole"]  # narrowed


def role_match(sso: LdapSso | OidcSso) -> str:
    match sso["__typename"]:
        case "LdapSecurity":
            return sso["ldapRole"]
        case "OidcSecurity":
            return sso["oidcRole"]
        case _ as never:
            assert_never(never)  # exhaustive
```

Both narrow correctly, the exhaustive `match` type-checks, and accessing the wrong branch's key is
`invalid-key`. This settles the interface/union output shape: a plain union of `__typename`-tagged
TypedDicts, narrowable and exhaustively matchable, with no pydantic `Discriminator` needed on the
output side.

**There is no runtime validation.** GraphQL is strongly typed and servers are assumed well-behaved,
so static typing is the guarantee. `ty` already catches every _input_ mistake — wrong leaf types,
unknown keys, missing required keys, keys assigned after construction, the
`Callable[[Input], None]` mutate-in-place pattern, and `@oneOf` two-key violations — all verified.
Outputs are the parsed JSON, typed by the generated TypedDicts and trusted.

What remains at runtime is **transformation, not validation**, at paths the generator knows
statically:

| Runtime work                | Why it cannot be static                                                 |
| --------------------------- | ----------------------------------------------------------------------- |
| Custom scalar coercion      | `SecondsSinceEpoch` int → `datetime` is a value change, not a check     |
| Custom scalar serialization | `datetime` → int on the way out                                         |
| `@required`                 | the one assertion the schema provably cannot make; ~free `is None` test |
| Open-enum sentinel          | only when `open_enums` is on; Atoti sets `set()`, so nothing            |

Measured against whole-output pydantic validation on identical payloads: **3.7x faster small,
18x faster large** (669 µs → 37 µs), because the transform touches only the paths that need work
rather than walking every key at every level. `@required` also gets a _better_ error —
`RequiredFieldError: dataModel.cube`, a clean path, instead of a pydantic loc tuple.

Consequences:

- Scalars are configured as a `(type, parse, serialize)` triple of plain callables. Atoti's are
  already exactly that behind a thin wrapper: `_timestamp.py`'s `_parse_timestamp`/`_to_timestamp`,
  `_constant.py`'s `_validate` (line 222) and `_serialize` (line 374).
- No `ConfigDict`, no `TypeAdapter`, no `Annotated` validators in generated code. Plain
  `typing_extensions` TypedDicts — inputs `closed=True` with per-key `Required`/`NotRequired`,
  outputs left open for forward compatibility.
- Struct is a `cast`; the output TypedDict _is_ the input TypedDict.
- `@oneOf` needs no pydantic `Discriminator` at all — `closed=True` plus `ty` cover it.
- `plugin.py:287`'s `_wrap_validation_error` has nothing to wrap and disappears.
- **Inputs need no serialization pass.** With no case conversion the input dict _is_ the wire dict;
  only scalar paths are touched. `NotRequired` keys are simply absent.

Two things to get right when emitting transforms: decide deliberately whether they **mutate the
executor's dict in place** (fastest, and fine for a response we own — but document it, since a
caller caching or retrying the raw response would see mutated values), and note the asymmetry that a
_missing_ key surfaces as `KeyError` at access while a _null_ one raises `RequiredFieldError`.

#### Operations needing nothing are pure passthrough

**59 of 152 operations need no transform at all** — no `@required`, no custom scalar, no struct, no
open enum. For those the reflective build returns `None` (verified: `build(Col) is None`), so the
generated method has _literally nothing_ between the executor and the caller:

```python
def get_x(self, variables, /, *, options=None):
    return self._execute(
        _GET_X, variables, options
    )  # no transform, no copy, no wrapper
```

The response dict goes straight through as the typed output — identical to what a hand-written client
would do. This is not an optimisation to add later; it falls out of the pruning the builder already
performs, and it means 39% of this client pays zero runtime cost for the whole design.

#### What the emitted traversal actually looks like

The worry that this gets unwieldy is worth checking, so here is the measured distribution across the
real 152 operations:

- **59 need no transform at all** — the generator emits nothing and the client method returns the
  cast directly.
- **Median work per operation: 1** site.
- **Worst case: `GetLevelOrdering`** — 5 `@required`, one struct, 7 levels deep.

That worst case, plus the worst list case, emit this in full:

```python
# runtime, shipped once
def _require(value, path, /):
    if value is None:
        raise RequiredFieldError(path)
    return value


# generated: shared, because several operations reach this struct payload,
# and emitted at all only because Ordering.first is a [ScalarConstant!]!
def _t_Ordering(d, /):
    if (first := d.get("first")) is not None:
        d["first"] = [parse_scalar_constant(x) for x in first]


# generated: GetLevelOrdering — the deepest operation in the document
def transform_GetLevelOrdering(d, /):
    dataModel = _require(d["dataModel"], "dataModel")
    cube = _require(dataModel["cube"], "dataModel.cube")
    dimension = _require(cube["dimension"], "dataModel.cube.dimension")
    hierarchy = _require(dimension["hierarchy"], "dataModel.cube.dimension.hierarchy")
    level = _require(hierarchy["level"], "dataModel.cube.dimension.hierarchy.level")
    _t_Ordering(level["ordering"]["value"])
    return d


# generated: GetMemberProperties — same depth, plus a list of scalars
def transform_GetMemberProperties(d, /):
    ...  # identical descent
    for p in level["memberProperties"]:
        p["value"] = parse_scalar_constant(p["value"])
    return d
```

Run and verified: the happy path coerces, a null at depth 3 raises
`RequiredFieldError: dataModel.cube.dimension`, and the list case coerces each element.

Four properties keep it small, and the emitter should preserve all four:

1. **Emit nothing for types needing nothing** — 39% of operations, and it prunes whole subtrees.
2. **Flat local-variable descent**, not a generic recursive walker. The code reads like the GraphQL
   selection it mirrors, which also makes golden-file review tractable.
3. **Shared transforms for structs and fragments**, emitted once.
4. **Paths are literals at emit time**, so good error messages cost nothing.

Where it _would_ grow: an operation selecting inline fragments on an interface needs a `__typename`
branch inside the transform, and nested lists-of-lists would nest loops. Neither appears in the
current document, but both should be handled rather than assumed away.

#### Inlined or a generic walker? Measured — inlined, and why Python beats Rust here

The alternative is one small generic walker interpreting an emitted plan per operation, trading
speed for less generated code. Both were built and benchmarked on the same payloads:

|                            | small (5×4)   | large (100×20)      |
| -------------------------- | ------------- | ------------------- |
| pydantic (Rust core)       | 12.9 µs       | 693.8 µs            |
| generic walker over a plan | 6.8 µs — 1.9x | 84.3 µs — **8.2x**  |
| inlined generated code     | 3.2 µs — 4.0x | 38.9 µs — **17.8x** |

**Interpreted Python beats a Rust validator because the algorithm differs, not the language.** On the
large payload pydantic checks every key at every level — roughly 2,200 leaf validations — while the
transform touches 102 sites. About 20x less work, and about 18x faster: the ratio is the
explanation. If full structural validation were needed, pydantic's Rust core would win comfortably;
it is only redundant because static typing already covers it.

**Recommendation: inlined.** It is 2.2x faster than the generic walker and — the part that decides
it — _not meaningfully more code_. The plan data structure costs about what the inlined statements
cost; for `GetLevelOrdering` the flat descent is 7 statements against 5 nested `Node` constructions.
The generic approach moves code from statements into data rather than removing it, so its stated
benefit does not materialise at this scale. Inlined also gives real stack traces pointing at a named
generated function, instead of every failure surfacing inside one `apply()` frame.

The generic walker's one genuine advantage is blast radius: it is a single thing to get right, where
a faulty emitter is wrong 152 times. Golden-file tests plus `compile()`-checking the emitted module
cover that, and an inlined bug is at least isolated to one operation. Keep the walker in mind as a
fallback if emitted size ever becomes a real problem; do not build both.

#### Why the walker's plan is redundant — and why that settles it

`GET_LEVEL_ORDERING` is ugly for a reason worth naming: **it is a second encoding of a structure the
generated TypedDicts already describe.** Pydantic needs no such artifact because for it _the types
are the plan_ — `TypeAdapter` introspects `__annotations__` at construction and compiles a validator
from them. There is one source of truth.

That gives three non-redundant designs, and one redundant one:

|                             | when the plan is derived | from what      | needs types at runtime?           |
| --------------------------- | ------------------------ | -------------- | --------------------------------- |
| pydantic                    | import time (JIT)        | the types      | yes                               |
| derive-from-types ourselves | import time (JIT)        | the types      | yes                               |
| **inlined**                 | **codegen time (AOT)**   | **the schema** | **no**                            |
| generic walker              | never — hand-emitted     | nothing        | no, but needs a hand-written plan |

The walker is the only one that defers work to runtime _without_ access to type information, so it
must be handed a description of something already described elsewhere. Two artifacts for one shape,
free to drift. That is a stronger argument against it than the 2.2x.

Inlined is simply ahead-of-time compilation of what pydantic does just-in-time: the generator reads
the schema, works out what needs doing, and emits the answer as code.

#### Runtime dependencies: `typing_extensions` only

**Superseded note.** An earlier revision of this plan reached _zero_ third-party runtime
dependencies by putting the generated TypedDicts behind `TYPE_CHECKING` with
`from __future__ import annotations` — verified working, with the client importing while
`typing_extensions` was blocked entirely, and `ty` still checking fully.

**Choosing reflection gives that up**, and the trade is worth stating plainly: reflection reads
`__annotations__` at runtime, so the TypedDicts must exist at runtime, and they are declared with
PEP 728 `closed=` — the one thing stdlib `typing` lacks. Dropping `closed=` is not an option, since
it is what makes `ty` reject a two-key `@oneOf`.

So `typing_extensions` is a runtime dependency, accepted as an extension of the standard library.
Everything else the runtime needs — `TypedDict`, `Required`, `NotRequired`, `Unpack`, `Literal`,
`Annotated`, `Protocol` — is stdlib, verified on the interpreter.

If zero dependencies ever matters more than types-as-single-source-of-truth, the inlined design plus
`TYPE_CHECKING`-gated types is the way back, and it is recorded above as measured and working.

#### The third option: pydantic with `SkipValidation` everywhere it is not needed

Keep pydantic, but wrap every field needing no work in `SkipValidation[...]` so only scalars,
`@required` and structs are actually processed. It works, and recovers most of the gain:

|                            | small (5×4)   | large (100×20)      |
| -------------------------- | ------------- | ------------------- |
| pydantic, full             | 12.4 µs       | 678.2 µs            |
| pydantic, `SkipValidation` | 6.8 µs — 1.8x | 109.3 µs — **6.2x** |
| inlined                    | 2.9 µs — 4.2x | 46.8 µs — **14.5x** |

Verified: it genuinely prunes (a skipped subtree passes `"not a list"` through untouched), it still
coerces the scalar, and **`ty` sees straight through it** — `SkipValidation[int]` resolves to `int`,
bad literals are caught, wrong-type operations are flagged. So static safety is fully intact.

**Still not preferred, for three reasons beyond the 2.3x:**

1. **It reinstates pydantic as a runtime dependency**, undoing the zero-dependency runtime.
2. **It is more generated output, not less.** Every field needing _nothing_ must be wrapped —
   roughly 90% of them — whereas transforms leave 59 of 152 operations emitting nothing at all. The
   intuition that annotating is cheaper than emitting code is backwards here.
3. **The generator analysis is identical either way** — it must still determine which fields need
   work. Only the emission differs, so this saves no analysis.

The residual 2.3x comes from pydantic still walking the containers en route, constructing new dicts
instead of mutating in place, and crossing the Rust↔Python boundary once per scalar callback.

Worth keeping as the fallback if the transform emitter proves troublesome: it is a smaller step from
a types-only generator than the walker is.

### Inputs — TypedDicts

`NotRequired` keys replace ariadne's `UNSET` sentinel: an absent key is simply not in the dict, and
since there is no case conversion the dict _is_ the wire payload. No serialization pass, no
`dump_python`, no sentinel.

Generated input TypedDicts are declared `closed=True` with an explicit `Required[...]` or
`NotRequired[...]` on **every** key — never a class-level `total=`. GraphQL inputs freely mix
required and optional fields, so per-key markers read better than a class default plus implicit
exceptions, and they make the generator's output self-describing.

Plain `typing_extensions` throughout — no pydantic anywhere in generated code. `plugin.py:170`'s
`Unpack[Empty]` hack is retired simply because the new generator emits no `**kwargs` at all.

### Default values

Counted from the schema AST (grep over-reports: two hits are `= ?` inside SQL example strings):

| Where                               | Count  | Handling                                                                |
| ----------------------------------- | ------ | ----------------------------------------------------------------------- |
| Input fields with a default         | **15** | `NotRequired`, value not baked in — see below                           |
| Field arguments with a default      | 0      | Baked into the document at codegen; invisible in Python                 |
| Variable definitions with a default | 0      | None in `operations.graphql`                                            |
| Output fields                       | n/a    | GraphQL has no output defaults; outputs are whatever the server returns |

**The rule a naive generator gets wrong:** all 15 are declared **non-null** yet have a default —
`isParameterTable: Boolean! = false`, `feeding: CubeFeeding! = ENABLED`,
`excludedLevelIdentifiers: [LevelIdentifier!]! = []`. In GraphQL a non-null input field _with_ a
default is **optional for the client**; the `!` constrains the value, not whether you must send one.
Reading `!` as `Required[...]` would force callers to pass values the server would happily supply.

So: a field is `Required[T]` **only** when it is non-null _and_ has no default. Nullable or
defaulted ⇒ `NotRequired[...]`. (There are currently 0 nullable-with-default fields, but the rule
should be written to cover them.)

**Do not bake the default value into the generated Python.** Omit the key and let the server apply
it. Baking it in pins a server-side default that may later change, and the client would then send an
explicit value that silently diverges from the server's intent — `Ordering.stringComparison`'s
default is a nested object (`{digits: LEXICOGRAPHIC, letterCase: SENSITIVE}`), which makes the drift
risk concrete. Record the default in the field's comment instead.

### Method signature — variables nested, executor options typed

Variables use the same mechanism as everything else: one functional TypedDict per operation. They are
passed as a **positional-only parameter** rather than splatted with `Unpack`, which removes the
collision problem entirely and leaves room for library and executor parameters.

```python
GetXVariables = TypedDict(
    "GetXVariables",
    {
        "cubeName": Required[str],  # String!      non-null, no default -> required
        "limit": NotRequired[
            int
        ],  # Int = 10     defaulted -> optional, server applies 10
        "tag": NotRequired[str],  # String       nullable -> optional
    },
    closed=True,
)

OptionsT = TypeVar("OptionsT", default=None)  # PEP 696 default


class Executor(Protocol[OptionsT]):
    def __call__(
        self, request: GraphQLRequest, /, options: OptionsT | None
    ) -> Mapping[str, object]: ...


class GraphqlClient(Generic[OptionsT]):
    def get_x(
        self, variables: GetXVariables, /, *, options: OptionsT | None = None
    ) -> GetXOutput: ...
```

The consumer declares what its executor accepts, and the client is parameterised by it:

```python
Httpx2Options = TypedDict("Httpx2Options", {"timeout": NotRequired[float]}, closed=True)
client: GraphqlClient[Httpx2Options] = ...
client.get_x({"cubeName": "c"}, options={"timeout": 5.0})
```

Verified with `ty`:

- **The clash is gone by construction.** `get_x({"cubeName": "c", "options": "…", "on_output": "…"})`
  type-checks: an operation may declare variables named `options` or `on_output` because they live
  inside the nested dict. Positional-only placement means even the parameter name cannot be shadowed.
- Executor options are checked: a typo is `invalid-key` on `Httpx2Options`, a wrong type is
  `invalid-argument-type`. Pass-through is typed, not `**kwargs: Any`.
- A missing required variable is still `missing-typed-dict-key`.
- The PEP 696 default means an executor with no options needs no parameter — bare `GraphqlClient`
  works.

This also gives the batching plugin a clean home for `on_output` (gap 1 below) as another
keyword-only parameter, with no reserved-name problem.

The cost versus `Unpack`: call sites write `get_x({"cubeName": "c"})` rather than
`get_x(cubeName="c")`. Slightly more punctuation, in exchange for no collisions, typed executor
pass-through, and a variables type that can be built and reused.

**Where an operation's default values live.** A default declared on a parametrized operation is
_not_ lost — it simply lives in the document rather than in Python, and that is the correct place
for it. Given `query GetX($limit: Int = 10)`, the `= 10` is part of the document text sent to the
server. Per the spec's `CoerceVariableValues`, when the client omits `limit` from the variables map
the server substitutes 10:

| Call                                      | Variables sent                     | Value the server uses                 |
| ----------------------------------------- | ---------------------------------- | ------------------------------------- |
| `get_x({"cubeName": "c"})`                | `{"cubeName": "c"}`                | `limit = 10` — the document's default |
| `get_x({"cubeName": "c", "limit": 5})`    | `{"cubeName": "c", "limit": 5}`    | `limit = 5`                           |
| `get_x({"cubeName": "c", "limit": None})` | `{"cubeName": "c", "limit": null}` | `null` — **not** the default          |

So the default is honoured on every call; it just never needs a Python representation. What we
deliberately do _not_ do is have **Python** substitute 10 before sending — that would pin the
document's default in a second place and let the two drift, exactly the problem already avoided for
input fields.

Note the third row: absent and explicit-null are different, and `NotRequired[T | None]` expresses
that distinction precisely — omit for the default, pass `None` for a real null.

The one genuine loss is introspection: a caller cannot read the default out of the Python signature.
It is recorded in the generated comment, and it is visible in the operation the consumer wrote.

**Rejected alternative: `**variables: Unpack[GetXVariables]`.** It was prototyped and verified to
work — `ty`reports`missing-argument`, `unknown-argument`and`invalid-argument-type`correctly, and
a prebuilt dict splats with`**`. It was dropped for one reason: **variable-name collisions\*\*. A
client-level keyword parameter (`options`, `on_output`) would shadow a GraphQL variable of the same
name, forcing the generator to detect collisions and fail. Nesting the variables removes the problem
by construction, and buys typed executor pass-through. The cost is punctuation:
`get_x({"cubeName": "c"})` rather than `get_x(cubeName="c")`.

Two caveats that survive into the chosen design:

1. Keyword-only placement for `options` comes free, replacing `plugin.py:161`'s
   `_force_keyword_only_args`.
2. The functional-TypedDict naming constraint (string name must equal variable name) applies to every
   emitted TypedDict, `*Variables` included.

### `@oneOf` — a union of single-key `closed=True` TypedDicts

```python
class _MeasureDefinitionSum(TypedDict, closed=True):
    sum: Required[SumMeasureDefinition]


class _MeasureDefinitionConstant(TypedDict, closed=True):
    constant: Required[ConstantMeasureDefinition]


MeasureDefinition: TypeAlias = _MeasureDefinitionSum | _MeasureDefinitionConstant
```

`ty` rejects `{"sum": …, "constant": …}` against that union, which is the whole enforcement story now
that there is no runtime validation. Two supporting facts, both verified:

- `total=` could never have expressed this. It governs whether _declared_ keys are required, never
  whether _undeclared_ keys are allowed — a distinct axis, which is why PEP 728 exists.
- `typing_extensions==4.15.0`, **already the repo's pin**, supports `closed` and `extra_items`, and
  `ty` understands them with no complaint about the keyword.

No pydantic `Discriminator`: it was only ever an optimisation for a validation pass that no longer
happens. Note `__typename` is **not** available here either — GraphQL input types do not carry one,
so the single key is the only discriminator, and it is checked statically.

**Inputs are `closed=True`; outputs are not.** Closing outputs would be actively wrong: a server
adding a field would break older clients, and forward compatibility matters more than rejecting a
key we never selected.

### Struct — an implementation of the GraphQL Struct RFC

Not an Atoti invention: this is the [Struct RFC](https://rfcs.graphql.org/rfcs/Struct/), which
proposes a `struct` keyword for a composite type usable symmetrically as **both input and output** —
covering input unions, recursive data and complex filters without losing type safety. The schema's own
comment says as much, pointing at the RFC and noting the interface workaround will be replaced once
it lands.

Status matters for how much to lean on it: **RFC 0 / Strawman, no champion**, document last updated
November 2023. So this is a recognised gap with a written-up proposal, not an imminent standard.

Until then the schema encodes it as an interface carrying an `Any`-typed payload:
`interface Struct { value: Any! }`, where `type FooStruct implements Struct` means `FooStruct.value`
has the shape of `input Foo`. With TypedDicts that falls out naturally — input and output are the
same type — so the output TypedDict _is_ the input TypedDict and the field is a cast.

This is worth supporting as a plugin rather than hardcoding: the convention (interface name, suffix
rule) is configurable, so any project using the same workaround gets it, and if the RFC ever lands
the plugin is where `struct` support would go.

### Type inference — `graphql-core`'s `TypeInfo`, verified against the real schema

We do **not** need to write type inference. `TypeInfo` + `TypeInfoVisitor` supplies all of it. This
was checked empirically (graphql-core 3.2.6, the 21 real `.graphqls` files, the real
`operations.graphql`), not assumed:

- The merged schema builds (347 types) and **all 174 operations validate with 0 errors** once the
  client directives are declared.
- Walking `GetHierarchyDescription` yields, for every field, the parent type, the resolved type
  _with wrappers_ (`String!`, `[Table!]!`, `Cube`), the field's own directives, and its argument
  definitions — `dataModel → cube → dimension → hierarchy → description` resolves correctly at
  every level.
- `ti.get_input_type()` / `get_argument()` resolve field arguments, directive arguments and
  variable definitions.
- **Interface chains work correctly** — the case ariadne-codegen gets wrong. In `GetRoleMappingItem`,
  `... on OidcSecurity` narrows to `OidcSecurity`, and `schema.get_possible_types()` resolves
  `SsoSecurityWithRoleMapping → [LdapSecurity, OidcSecurity]` and `Table → [ExternalTable,
InMemoryTable]`. So the `__typename`-discriminated union can be generated properly and the
  repeated-fragment-spread workaround deleted.
- `schema.is_sub_type(Struct, t)` finds all 9 `*Struct` implementors, and `removesuffix("Struct")`
  resolves each to a real input type — the convention holds mechanically, replacing the monkeypatch.

Three caveats the implementation must handle:

1. **Do not bump `graphql-core`; declare `@oneOf` yourself.** 3.2.6 (the repo's pin) rejects the
   schema with `Unknown directive '@oneOf'`, but bumping to 3.2.12 breaks ariadne-codegen 0.16.0 at
   run time. Declare the directive when `specified_directives` lacks it and read one-of-ness off the
   AST — see the packaging section above for the measurements.
2. **Client directives must be declared in the validation schema** (`@required`, and `@mixin` while
   the old annotations still exist), then stripped before the document goes on the wire.
3. **`TypeInfo` does not descend into fragment spreads.** At a spread point it reports the enclosing
   type, sometimes wrapped (`[Join!]!`), so the generator resolves fragments by name and unwraps
   list/non-null before composing.

### Error types — take the spec's wire shape, not graphql-core's class

Worth being precise about, because graphql-core's
[error module](https://graphql-core-3.readthedocs.io/en/latest/modules/error.html) looks like
something to reuse and is really two different things.

**The wire shape: yes, copy it — but it is the _spec's_, not graphql-core's.**
`graphql.error.GraphQLFormattedError` is a plain `TypedDict` mirroring
[spec §7.1.2](https://spec.graphql.org/October2021/#sec-Errors):

```python
class GraphQLFormattedError(TypedDict, total=False):
    message: str
    locations: List[FormattedSourceLocation]  # {"line": int, "column": int}
    path: List[Union[str, int]]
    extensions: GraphQLFormattedErrorExtensions
```

That is the whole of what a server may send, so re-declaring it costs ~10 lines of
`typing_extensions` and creates no dependency. It is a TypedDict already, which fits the rest of the
design exactly. Declare it **more precisely than graphql-core does**: the spec requires `message`,
so it is `Required[str]` with the other three `NotRequired[...]`, where graphql-core's blanket
`total=False` makes even `message` optional.

**The class: no.** `GraphQLError` carries eight attributes and **four of them are server-side
execution concerns** that a client cannot populate and we could not construct without graphql-core:

| attribute                                    | client-side?                                                |
| -------------------------------------------- | ----------------------------------------------------------- |
| `message`, `locations`, `path`, `extensions` | yes — the wire fields above                                 |
| `nodes` (`List[Node]`)                       | **no** — AST nodes; we have no AST at runtime               |
| `source` (`Source`)                          | **no** — ditto                                              |
| `positions` (`Collection[int]`)              | **no** — offsets into that `Source`                         |
| `original_error`                             | **no** — the resolver exception that caused it, server-side |

Inheriting it would mean importing graphql-core at runtime to get four fields we can never fill.

So the runtime ships a small hierarchy of its own, and only one member of it corresponds to
anything graphql-core has:

```python
class GraphQLError(Exception):          # one entry from the response's `errors` array
    formatted: GraphQLFormattedError    # verbatim, so nothing the server sent is lost
class GraphQLMultiError(Exception):     # the whole array; raised since errors always raise
class HttpError(Exception):      # non-2xx — the executor's mapping surfaces here
class ProtocolError(Exception):  # body was not JSON, or not a GraphQL response
class RequiredFieldError(Exception):    # ours: a @required path was null. Not from the server.
```

`RequiredFieldError` is the one with no analogue at all — it is a _client_ assertion about one
query, which is the whole point of `@required`, and it carries a dotted path
(`dataModel.cube.dimension`) rather than the spec's list-of-segments, because the generator knows
the path as a literal at emit time.

**Two notes on the wire fields:**

- **`path` is what batch error attribution uses.** Its first segment is the aliased root field
  (`createMeasure_2`), and the alias suffix already encodes the batch index, so mapping an error to
  the operation that produced it is string handling — no AST, consistent with the rest of batching.
- **`locations` is nearly useless to us and should not be surfaced as though it were not.** It
  points into the document _we_ generated, which the caller never wrote and, under batching, never
  existed before the merge. Keep it in `formatted` for completeness; do not build an API on it.

**On the generator side, use graphql-core's errors fully** — it is a generator dependency, so there
is nothing to avoid. `GraphQLSyntaxError`, the errors from `validate()` (which already carry
`locations` and `source`), and `print_error` / `print_source_location` for the caret-underlined
excerpt are exactly what the diagnostics design above calls for. **The split is the dependency
boundary**: graphql-core's error machinery is reused wholesale where it is free, and re-derived from
the spec where it would cost a runtime dependency.

### Emission — build an `ast`, unparse it

Generated code is built as a real :mod:`ast` tree and unparsed, never assembled from strings. An
earlier draft chose string emission through typed helpers, on the grounds that `ast.unparse` gives
poor formatting. That was the wrong trade: formatting is `ruff format`'s job and runs over the
output anyway, whereas hand-built strings cannot be syntactically wrong *by construction* and make
quoting a recurring source of bugs. Two were hit within minutes of trying it.

The tree is `compile()`d before being written — stdlib, free — so a bad node fails generation rather
than reaching a golden file.

Two consequences worth knowing:

- **`ast.unparse` drops comments.** Anything worth saying to a reader of the generated code must be
  a docstring, which is a real node. Module and class docstrings carry the explanations that would
  otherwise have been comments.
- **Forward references must be quoted outside-in.** Quoting just the unknown name produces
  `"X" | None`, which is a runtime `TypeError` because `|` on a string is not a type union. The
  whole annotation is quoted instead — `Required['X | None']` — which keeps `Required[...]` a real
  node so `__required_keys__` is still computed at class creation. This is not optional: 25 input
  types are recursive.

Emit ruff-clean, deterministic code and **drop the exclusions** the current setup needs
(`extend-exclude`, `[tool.ty.src] exclude`, pylint `ignore-paths`, three coverage `omit`s).

### Batching — split the mechanism from the policy

Batching decomposes into three things, and they do not belong in the same place:

|                |                                   | where                        |
| -------------- | --------------------------------- | ---------------------------- |
| **merge**      | N requests → 1 request            | pure → **core lib**          |
| **split**      | 1 response → N responses          | pure → **core lib**          |
| **collection** | which operations, dispatched when | scheduling → **ActivePivot** |

Merge and split are pure functions over data — the precomputed-template design — so they are sans-io
core regardless. Collection is where every awkwardness lives (futures, `ContextVar`,
`flush_prematurely`, the sync/async split, the query problem), and **deferral is IO scheduling**,
which a sans-io library has no business owning.

**The core primitive: `gather` for GraphQL, with no deferral at all.**

```python
b = batch(client.delete_table.request(v) for v in names)  # pure, no IO
raw = my_transport(b.request())  # caller's IO — sync or async
outs = b.parse(raw)  # typed tuple
```

Verified with `ty`, typed by overloads exactly as `asyncio.gather` is:

| case                                     | inferred                                                             |
| ---------------------------------------- | -------------------------------------------------------------------- |
| heterogeneous, 2–N operations            | `tuple[DeleteTableOutput, CreateMeasureOutput]` — exact per position |
| homogeneous, **n known only at runtime** | `tuple[DeleteTableOutput, ...]`                                      |

This answers both open questions at once:

- **Queries work**, because nothing is deferred — a batched query returns its value from `parse()`.
  Queries and mutations still cannot share a request, so the core rejects a mixed batch.
- **Async works with no second concept**, because the caller owns the await. The core never schedules;
  `b.request()` and `b.parse(raw)` are pure either side of whatever IO the caller performs.
- No futures, no `ContextVar`, no scope, no `flush_prematurely` in the library.

**The policy layer stays in ActivePivot, and it is genuinely load-bearing.** Checked against the
repo rather than assumed:

- **Batching is not public API.** `batch` appears **0 times** in the committed API snapshot
  `atoti/__resources__/api.json`, so `with client.batch(): session.create_table()` is a hypothetical,
  not a requirement. It need not be designed for now.
- **That form could not work today regardless, and the blocker is not GraphQL.** Every public
  `create_*` is a write followed by an immediate read-back:
  `session.create_table` (`session.py:580`) calls `tables.set(...)`, which is
  `self[key] = value; return self[key]`
  (`_collections/delegating_converting_mapping.py:153`) — the assignment runs the
  `createInMemoryTable` mutation via `_update_delegate` (`tables.py:126`) and the return runs the
  `findTable` **query** via `_get_unambiguous_keys` (`tables.py:118`). Inside a batch scope the
  read-back would fire before the mutation flushed — and queries and mutations cannot share a
  request anyway — so it would raise `KeyError` rather than return a `Table`. Supporting
  `with client.batch(): session.create_table()` therefore means changing what the SDK's write
  methods return, not extending the batcher. Out of scope, and out of the library's reach.
  This is exactly why the 16 real sites batch at the _delegate_ level, where nothing is read back.
- **The 16 `with mutation_batcher.batch():` scopes are all co-located** — the same
  `graphql_inputs = [...]` then `with batch(): for ...:` shape in `measures.py`, `cubes.py`,
  `_member_properties.py` and the rest. Each maps directly onto the deferral-free core `batch()`, and
  `validate_future_output` disappears with it: with no deferral you simply check the parsed outputs
  afterwards, so `create_delete_status_validator`'s callback becomes a plain loop.
- **But ambient state is consulted across layers, in two places that only make sense when a batch is
  open in an outer frame:**
  - `connected_session_client.py:48` passes `is_batching_mutations` as a callback **into the Py4J
    client** — a different subsystem asking whether a GraphQL batch is currently open.
  - `flush_prematurely()` is a **no-op unless an outer batch exists** (`submit()` already wraps itself
    in `with self.batch():`, so a lone mutation executes immediately). `udaf_measure.py:208` calling
    it is therefore evidence that it expects to run nested inside someone else's batch.

So the ambient `ContextVar` layer cannot be deleted. It stays in `atoti-client`, where it belongs:
it exists because of how the SDK layers its API and how Py4J interoperates, neither of which is
anything to do with GraphQL.

**Nothing in this repo changes behaviourally.** `OperationBatcher` keeps its class, its
`with mutation_batcher.batch():` scopes, its `ContextVar` and its `flush_prematurely()`; only its
_implementation_ changes, from re-parsing and re-aliasing documents on every flush
(`_merge_documents.py`, 161 lines of AST surgery) to calling the library's pure `merge`/`split` over
templates the generator precomputed. The Java-side benefit that makes this load-bearing — fewer cube
restarts because the server sees one request with full context — is preserved exactly, since the
merged document is the same document.

The 16 co-located scopes can migrate to the core `batch()` at leisure — cleaner, and it removes the
`validate_future_output` callback — while the ambient layer keeps serving the nested cases.

Roughly how today's 411 lines of `_batching/` divide: `_merge_documents.py` (161),
`_unmerge_output.py` (18), `_merge_variables.py` (19) and `_naming.py` (12) become core — and shrink,
since precomputed templates replace the AST work. `operation_batcher.py` (159) and
`batched_operation_future_output.py` (38) stay as Atoti's scheduling policy.

**Net:** the lib ships the elegant, universal, fully-typed half; the half that is awkward in async,
awkward for queries and needs ambient state is exactly the half that is Atoti-shaped. That is the
line, and the decomposition put it there rather than taste.

### Injected variables — the generic home for `dataModelTransactionId` / `dataTransactionId`

Atoti threads two such values through nearly every operation: `$dataModelTransactionId` appears
**160 times** in `operations.graphql`, and `dataModelTransactionId` / `dataTransactionId` are also
fields on ~25 input types. Today this is `plugin.py:101`'s `_process_variables` override, which
injects the value and asserts callers never set it by hand.

The generic concept is **an injected variable**: a GraphQL variable or input field whose value the
client inserts at request-build time rather than the caller passing it. Nothing about that is
transaction-specific — tenant ids, locales, request ids and trace ids have the same shape.

**Naming, decided on codebase evidence rather than taste:**

- **`inject` wins because it is already the verb for this exact operation here.**
  `inject(carrier=request.headers)` appears at `client.py:121` and `endpoint/_server.py:55` —
  OpenTelemetry's inject, automatically adding a value to an outgoing request, in the same file that
  constructs the GraphQL client. The attack-class connotation attaches to the noun ("an injection
  vulnerability"), not the adjective; "injected dependency" has been unambiguous for decades.
- **`provider` is taken.** 78 occurrences in the SDK, essentially all _authentication_ providers
  (OIDC, identity). `VariableProviders` would collide with `oidc_config`'s vocabulary.
- **`context` is taken twice.** In GraphQL it is the server-side execution context passed to
  resolvers; in Python it is `contextvars.ContextVar`, literally the mechanism backing this.
- **`external` is taken.** 127 occurrences in the schema, 425 in the SDK, meaning the remote
  DirectQuery database (`ExternalTable`, `ExternalDatabaseDiscovery`) or an outside identity
  provider (`externalRole`).
- **`scoped`** is reserved for the lifetime helper below.

**What gets matched.** Injected names match **operation variable definitions** and **input object
fields** — never field arguments. Confirmed in the real schema: the operation declares
`$dataModelTransactionId` and passes it to an argument named `transactionId`, so matching on
arguments would be both wrong and unnecessary.

Measured surface in Atoti: `dataModelTransactionId` is a field on **35 input types** and a variable
in **80 operations**; `dataTransactionId` is a field on **1**. Every occurrence is typed `ID`.

**Config names them; no per-site directive:**

```python
injector_names = ["dataModelTransactionId", "dataTransactionId"]
```

A directive would mean annotating 80 variable definitions plus 35 input fields — 115 new
annotations, reintroducing exactly the verbosity just eliminated for `@required` (221 → 0). It stays
auditable without one: the generated `*Variables` TypedDict visibly lacks the key, `ty` rejects any
attempt to pass it, and the generator emits a manifest of every site it touched. If a single
operation ever needs to pass one explicitly, an opt-out directive can be added then.

**Codegen effect.** The named variable is removed from the operation's `*Variables` TypedDict and
the matching field from input TypedDicts, so callers _cannot_ set it — `ty` reports
`unknown-argument`, strictly better than today's runtime assert. Injection sites are emitted
statically rather than discovered by walking values at runtime.

**The injectors are generated and precisely typed** — not a stringly-typed `dict[str, Callable]`.
The generator knows each injected name's GraphQL type, so it emits:

```python
VariableInjectors = TypedDict(
    "VariableInjectors",
    {
        "dataModelTransactionId": Required[Callable[[], str | None]],  # ID
        "dataTransactionId": Required[Callable[[], str | None]],  # ID
    },
    closed=True,
)


class GraphqlClient:
    def __init__(self, *, executor: Executor, injectors: VariableInjectors) -> None: ...
```

That answers the return-type question by construction: `closed=True` rejects a typo'd injector name,
`Required` rejects a forgotten one, and `ty` checks each getter's return type against the schema's.
`atoti-client` supplies two getters reading its existing `ContextVar`s.

Two rules for the generator:

1. **Verify type consistency and fail loudly.** An injected name spread over 35+ declaration sites
   must have one type everywhere, or a single injector signature is a lie. Both Atoti names are
   currently consistent (`ID`), so this is a guard, not a migration.
2. **`None` means omit**, matching `_process_variables`'s current `del variables[...]`. This makes
   "omit" and "send an explicit null" indistinguishable for injected variables; acceptable, since a
   variable whose null is meaningful should not be injected. Document it rather than adding
   a sentinel.

**Also worth lifting: the scope helper.** `atoti/_transaction/_transact.py` is already generic — it
takes `allow_nested`, `check_nesting`, `commit`, `rollback`, `create_context`, `start` and a
`ContextVar` keyed by session, and nothing in its body is about transactions. Lifting it needs three
substitutions: `SessionId` → an opaque scope key, `TransactionId` → a type parameter, and dropping
the `TRACER` span (opentelemetry is outside the dependency budget — expose an optional
enter/exit hook instead so `atoti-client` can re-attach its span). Atoti then instantiates it twice,
as it does today, and `_transaction/` shrinks to two thin configurations plus its public API.

### Plugins

```python
class Plugin(Protocol):
    directives: Sequence[str]  # SDL for client directives it adds

    def visit_operation(self, op: OperationIr, /) -> OperationIr: ...
    def runtime_imports(self) -> Sequence[str]: ...
```

Selected by an explicit config list — in this repo the root package depends on every workspace
member, so "auto-enable if installed" would always enable everything.

- **`batching`** — all mutations return `FutureOutput[T]`; the client gains `.batcher`; emits the
  precomputed `BatchableOperation` constants above. The 5 call sites needing a synchronous result
  keep `flush_prematurely()` + `.output()`.
- **`async_`** — emits `AsyncGraphqlClient` against `AsyncExecutor`. The core must not know async
  exists: request-building and output-validation are plain functions both clients call.
- **`injector_names`** — see above. Removes the named variables and input fields from generated
  types and injects them from registered injectors at call time.

---

## How this compares, and whether anyone else would want it

**ariadne-codegen is alive** — 0.19.0, issues filed as recently as March 2026, still pre-1.0 with no
stable-API promise. This repo pins 0.16.0, three minors behind. So this is not a rescue of an
abandoned tool; it has to justify itself on design.

Where it genuinely differs — updated for the final design:

|                        | ariadne-codegen                                                                        | this                                   |
| ---------------------- | -------------------------------------------------------------------------------------- | -------------------------------------- |
| Transport              | generates a client importing `httpx`                                                   | sans-io; the executor is the caller's  |
| Outputs                | nested pydantic `BaseModel`s                                                           | TypedDicts                             |
| Runtime validation     | full, via pydantic                                                                     | **none** — static typing only          |
| Runtime deps           | pydantic + httpx                                                                       | `typing_extensions`                    |
| Parse a large response | ~678 µs                                                                                | **~62 µs**                             |
| Interface chains       | [#254](https://github.com/mirumee/ariadne-codegen/issues/254), **open since Dec 2023** | correct, via `TypeInfo`                |
| Client non-null        | none (this repo abuses `@mixin`, 221×)                                                 | `@required`, enforced                  |
| Batching               | none                                                                                   | first-class, no runtime `graphql-core` |

The `httpx` point is not theoretical: this repo already regex-rewrites `httpx` → `httpx2` in
ariadne's copied templates (`plugin.py:407`). That hack _is_ the argument for sans-io.

**TypedDicts are about 2x cheaper than `BaseModel`s** — measured against an equivalent plain model:

|                            | TypedDict | BaseModel |       |
| -------------------------- | --------- | --------- | ----- |
| Validation, small (5×4)    | 12.6 µs   | 19.8 µs   | 1.57x |
| Validation, large (100×20) | 679 µs    | 1321 µs   | 1.95x |
| Read a 5-deep path         | 117 ns    | 238 ns    | 2.03x |
| Memory, one large result   | 529 KiB   | 1134 KiB  | 2.14x |

The validation gap widens with payload size, and attribute access is _slower_ than subscript
(pydantic reads go through `__dict__` machinery), which is the opposite of most people's intuition.
Do not oversell it though: 0.64 ms on a large response is noise against a 10–100 ms round-trip, so
performance is a secondary argument behind sans-io and correctness. It matters most for memory on
large held result sets and for high-throughput batching. The comparison is against a plain
`BaseModel`; ariadne's generated models carry aliases and config that would widen the gap further,
so treat 2x as indicative rather than a precise claim about ariadne.

**Where ariadne-codegen is ahead, and would stay ahead:** maturity and real-world use, subscriptions
over websockets, fetching the schema from an introspection URL rather than files, file uploads,
an existing plugin ecosystem and documentation, and attribute access — which many people simply
prefer to subscripts.

**The closest competitor is [Turms](https://jhnnsrs.github.io/turms/)**, not ariadne: pydantic v2,
built on graphql-core, transport-agnostic, extensible plugin pipeline. It already occupies much of
this niche, so the honest differentiation narrows to TypedDicts over models, batching, `@required`,
injected variables, and the dependency budget. (Its landing page does not detail the generated
shape; characterise it properly before making any public claim.)

**Would others find it useful?** For a specific audience, yes: anyone who cannot use `httpx`
(corporate HTTP wrappers, aiohttp/trio shops, forks like `httpx2`), teams running strict type
checkers who want narrowable unions and exhaustive matches, and anyone who wants request batching.
On batching specifically, an earlier draft of this plan undersold it. The _execution-plan_ benefit
does depend on a cooperating server (Atoti's `MutationUtil.isBatchFinalField`), but the larger benefit
does not: batching is **the type-safe answer to runtime-dynamic operations**, where every other Python
client makes you drop to an untyped document. That is universal.

The TypedDict choice remains the main thing people will either accept or reject — though the cost is
smaller than it looks, since editors autocomplete TypedDict keys and the alternative trades a measured
11x on response handling for attribute sugar.

Note the tension with scope: the features that would make this broadly useful — introspection,
subscriptions, uploads, docs — are exactly the ones deferred as non-goals. "Public-ready" is a
deliberate later investment, not a side effect of building it.

## Why the runtime has no pydantic

Pydantic can be removed from the runtime entirely, leaving **`typing_extensions` as the only
third-party dependency** (and even that goes once PEP 728's `closed` lands in 3.15). The route is the
same static-paths trick already used for injected variables, batch templates and uploads: the
generator knows exactly which paths carry a custom scalar, an enum, or a `@required` assertion, so it
can emit a targeted transform instead of handing the whole response to a validator.

Measured, same payloads as above:

|                | pydantic validate | generated transform |         |
| -------------- | ----------------- | ------------------- | ------- |
| small (5×4)    | 12.8 µs           | 3.5 µs              | 3.7x    |
| large (100×20) | 669 µs            | 37 µs               | **18x** |

The gap is far larger than the 2x against `BaseModel`, because pydantic walks _every_ key at every
level (plus `closed=True`'s extra-key checks) while the transform touches only the paths that need
work. `@required` survives with a _better_ error — `RequiredFieldError: dataModel.cube`, a clean
path, rather than a pydantic loc tuple.

**What it costs**, and it is real: structural validation. A server omitting a field is accepted
silently and surfaces later as a `KeyError`, where pydantic reports
`ValidationError at ('dataModel','database','tables',0,'columns')`. Input `@oneOf` enforcement also
becomes static-only, though `closed=True` plus `ty` already cover it for typed callers.

**What it takes:**

1. **Scalar config changes shape** — from a type path to a `(type, parse, serialize)` triple. Atoti's
   scalars are _already_ plain function pairs behind a thin wrapper: `_timestamp.py` has
   `_parse_timestamp` / `_to_timestamp`, and `_constant.py` has `_validate` (line 222) and
   `_serialize` (line 374), with `BeforeValidator` / `PlainSerializer` merely wrapping them. So this
   is exposing existing functions, not writing new ones.
2. The generator emits a per-operation transform: scalar coercion, `@required` checks, open-enum
   sentinel mapping, at known paths.
3. Input serialization likewise becomes emitted calls at known paths.

**Decision: no validation layer at all, and therefore no pydantic.** The same principle that says we
do not impose an HTTP client says we do not impose a validator; here we simply do not need one.
Because the emitted transform is an ordinary generated function, a validating variant remains
possible later as a plugin without redesigning anything — but nothing is built for it now.

The one risk to carry knowingly: a misbehaving or skewed server produces a `KeyError` deep in caller
code rather than a clean boundary error. This is the same in-sync assumption already made for
`open_enums`, and it rests on the same two facts — schema and server ship from one monorepo at one
version, and `Client._graphql_client` already gates on `has_compatible_server_api`.

For Atoti this buys speed and simplicity rather than dependency reduction, since the SDK depends on
pydantic regardless. The zero-dependency runtime is an argument for the library's other users.

## Gaps found auditing the current setup against this plan

Checked every `plugin.py` hook and every hand-written companion in `_graphql/`. Five things this
plan did not yet account for:

1. **Post-flush output validation on batched mutations.** Dropping ariadne's
   `validate_future_output` parameter loses `create_delete_status_validator`'s pattern — turning a
   `DeleteStatus.NOT_FOUND` into a `KeyError` when the batch flushes, used by every
   `_delete_delegate_keys` implementation. The batching plugin adds it back as
   `on_output: Callable[[Output], None]`, a keyword-only parameter alongside `options`. Nesting
   variables (above) means there is no reserved-name problem.
2. **Coverage pragmas.** `plugin.py:482` injects `# pragma: no cover` plus a `KEEP_MARKER` on three
   branches, paired with the `report_unnecessary_coverage_pragmas` tooling. Since this plan drops
   the linter/coverage exclusions on generated code, decide explicitly: emit pragmas, or keep the
   generated directory in coverage's `omit`.
3. **Unknown enum values — resolved: enums are closed, and there is no `open_enums` config.**

   Enums are emitted as `Literal[...]`, which is worth defending: `ty` gives real exhaustiveness
   over it, reporting `type-assertion-failure: Argument does not have asserted type Never` when a
   `match` omits an arm — the `case _ as never: assert_never(never)` pattern this repo relies on
   heavily enough to have a coverage rule for. `Literal` also drops the `.value` indirection:
   `level["type"] != "ALL"` rather than `level.type.value != "ALL"`.

   **They are closed, matching the client being replaced.** Checked rather than assumed:
   ariadne-codegen emits plain `class X(str, Enum)` with no sentinel and no openness option (its
   only enum settings are `enums_module_name` and `include_all_enums`), and pydantic **rejects** an
   unknown member — `M.model_validate({"d": "FUTURE_MEMBER"})` raises `ValidationError`. So today's
   client already treats an unknown member as an error, and matching that is both simpler and a
   behaviour-preserving migration.

   This deletes a whole subsystem that an earlier draft specified: the Relay
   `"%future added value"` sentinel, per-enum `open_enums`, the paired open/closed aliases for
   both-position enums, the allowlist validation, and the sentinel-mapping step of the emitted
   transform. One `Literal` alias per enum, used in both directions.

   **What forced the question, and why it dissolves — the Struct interaction.** With the Struct
   workaround an *input* type is also received, as the `value: Any!` payload, so an enum inside one
   is received too. Measured on the real schema:

   | | count |
   |---|---|
   | Struct implementors | 9 |
   | input types transitively inside a Struct payload | **70** |
   | enums transitively inside a Struct payload | **25 of 41** |
   | those a naive position census calls "input-only" | **23** |
   | genuinely input-only, once Struct is accounted for | **7** (not 30) |

   So the naive census in an earlier draft — 30 input-only, 4 output-only, 7 both — is wrong by 23:
   most "input-only" enums are in fact received. Had enums stayed open, a Struct payload type would
   have needed to be open when received and closed when sent *at the same time*, which one shared
   TypedDict cannot be; the alternatives were duplicating all 70 payload input types, or permitting
   the sentinel to be sent. Closing every enum removes the conflict entirely.

   The cost, carried knowingly: with no runtime validation an unknown member is not rejected the way
   pydantic rejects it today — it flows through as a string outside the `Literal`. That is the same
   in-sync assumption the rest of this design already makes, and it rests on the same two facts:
   schema and server ship from one monorepo at one version, and `Client._graphql_client` gates on
   `has_compatible_server_api`.

4. **`@deprecated`.** Zero uses in the schema today, but it is standard GraphQL and a public library
   should not ignore it. Cheapest useful handling: carry the reason into the generated comment.
5. **Generated names are load-bearing for hand-written code.** `_graphql/condition.py` (179 lines of
   Protocols) refers to `HierarchyIdentifier` and `HierarchyIdentifierFragment` by name. The naming
   scheme must keep fragments nameable and stable, or that file breaks silently.

6. **Fragments compose by inheritance** — exactly as ariadne does with `BaseModel`. A fragment spread
   becomes a base class, several spreads become several bases. No field expansion, no duplication.
   This depends on the class-syntax decision above; note that the DRY-looking _functional_
   alternative `TypedDict("X", {**Frag.__annotations__, ...})` is a trap — it works at runtime but
   `ty` rejects it outright (_"Keyword splats are not allowed in the `fields` parameter to
   `TypedDict()`"_) and then treats the result as having no keys, so every access becomes
   `invalid-key`.

   Also verified: **recursion works** via a forward-reference string
   (`"logical": NotRequired[list["Cond"]]`), which matters because the schema has **25 recursive
   input types** — the whole `*Condition` / `*LogicalCondition` family. Both pydantic and `ty` handle
   it, `ty` catching a wrong element type inside the recursive list.

7. **Nominal typing is used in 12 places and must be rewritten.** TypedDicts are structural and
   cannot appear in `isinstance()` or class patterns at all. Measured across `atoti-client`
   (195 distinct names imported from `_graphql`):

   - **6 class patterns** in `_identification/{column,hierarchy,level}_identifier.py`, e.g.
     `case ColumnIdentifierFragment() | ExternalColumnIdentifierFragment():` versus
     `case GraphqlColumnIdentifier():` inside `_from_graphql`.
   - **6 `isinstance()` calls**, all in `security/role_mapping.py`, discriminating interface
     implementations (`GetRoleMappingItemSecuritySsoLdapSecurity` vs `…OidcSecurity`).

   The `isinstance` six become `__typename` narrowing, which is _better_ — it discriminates on the
   actual wire value rather than on a Python class. The class-pattern six discriminate an output
   fragment against an input type, which have genuinely different keys (`table`/`name` versus
   `table_name`/`column_name`); those are best split into two functions rather than one union, since
   `ty` does **not** narrow TypedDict unions on `in` checks.

All of the above are settled under **Resolved** below — `@required` on a list, partial errors, batch
error attribution, coverage pragmas and `@deprecated` — except the last, which is settled here:

**Generated module layout: shared modules for schema-derived types, one module per operation.**

```
_graphql/
  __init__.py       # re-exports the public names, as today
  enums.py          # 41 Literal aliases (+ closed variants for both-position open enums)
  scalars.py        # scalar type aliases and the parse/serialize bindings from config
  inputs.py         # 100+ input TypedDicts, incl. @oneOf unions and recursive ones
  fragments.py      # fragment TypedDicts — the ones hand-written code names
  operations/<OperationName>.py   # variables + output TypedDicts + transform + wire constants
  client.py         # GraphqlClient, one method per operation
```

Schema-derived types are shared because they are shared in fact — an input type used by 12
operations must be one type, and the 195 names `atoti-client` imports today are overwhelmingly
these (117 enum/scalar/helper + 56 input + 13 fragment against 8 nested-selection). Operation-derived
types go one-per-module because they are used by exactly one operation and because it makes
golden-file diffs local: editing one operation rewrites one file, which matters when the snapshot is
the test. The `snapshot` fixture compares directories, so the file count costs nothing there.

Eager import of ~152 small modules is the one cost; it is bounded and measurable, and milestone 8
should record `import atoti` timing before and after. If it ever matters, `client.py` is the only
thing that must be imported eagerly and the operation modules can move behind `__getattr__`.

**Explicit non-goals for v1**, so the `Executor` protocol is not painted into a corner: subscriptions
(zero today; they need an iterator-shaped executor), `@defer`/`@stream`, and persisted queries.

## File uploads under sans-io

Not needed today — the schema has no `Upload` scalar and no file mutations — but worth confirming
the seam can absorb it, since it is the obvious thing that looks like it would break sans-io.

It does not, because the [GraphQL multipart request spec](https://github.com/jaydenseric/graphql-multipart-request-spec)
is a _structure_, and only the final encoding is IO. The library computes the structure; the executor
encodes it:

```python
@final
@dataclass(frozen=True, kw_only=True)
class FilePart:
    name: str  # multipart field name: "0", "1", …
    filename: str | None
    content_type: str | None
    content: bytes | IO[bytes]  # the library never reads it


@final
@dataclass(frozen=True, kw_only=True)
class GraphQLRequest:
    document: str
    operation_name: str
    variables: Mapping[str, object]  # file positions nulled out, per the spec
    file_parts: Sequence[FilePart] = ()  # empty -> plain JSON POST
    file_map: Mapping[str, Sequence[str]] = frozendict()  # part name -> variable paths
```

Crucially the library must **not** encode to bytes itself: `httpx`, `aiohttp` and `requests` all take
a `files=` mapping and stream it, whereas producing one `bytes` blob would buffer whole files in
memory. Handing over the structure keeps that choice with the caller, which is the point of sans-io.

**Typing `Upload` needs no special case.** With no validation layer it is simply a class the
runtime ships, configured like any other scalar:
`scalars = {"Upload": ScalarConfig(type="graphql_codegen.runtime.Upload", ...)}`. The generator
lifts instances out of the variables into `file_parts` at the statically-known paths.

**Finding the files** reuses the trick already used for injected variables and batch templates: the
generator knows statically which variable paths are `Upload`-typed, so it emits those paths rather
than walking arbitrary values at runtime. Lists (`[Upload!]!`) and uploads nested inside input
objects fall out of the same mechanism, with list indices resolved when the request is built.

## Milestones

Each is independently reviewable.

1. **Skeleton + config + schema loading.** Package created at `python/graphql-codegen/`, registered
   per the table above, `uv lock` run, and importable. **Leave `graphql-core` pinned at 3.2.6** and
   declare `@oneOf` in the validation schema instead (bumping breaks ariadne-codegen at run time —
   see the packaging section). Then port
   `_print_merged_schema` from `_generate_client.py:60-90` — sorted glob, concat, parse, strip
   `EXTENDED_AT_RUNTIME_PLACEHOLDER` (keep the `assert removed_placeholder` tripwire and the
   "Keep in sync with `RemoveRuntimeExtensionPlaceholderVisitor.java`" comment), but pass a real
   `Source(text, name=str(path))` per file instead of `linesep.join(...)` so diagnostics can locate
   errors. Declare `@required`
   and tolerate the schema's `@auth(role:)`.
   Gate: schema builds and all 174 operations validate with 0 errors — already demonstrated.
   Gate: `uv run ty check`, `uv run ruff check python/` and `uv run python -m pylint python` are all
   clean with the new member in the workspace.
2. **IR + type mapping.** Drive it from `TypeInfoVisitor`, per the verified probe above, so this
   milestone is mapping and naming only — no type inference of our own. Scalars, enums, lists,
   non-null, inputs, `@oneOf` (via `is_one_of`, emitted as the discriminated union above),
   interfaces including `implements` chains, unions, struct. Unit tests per mapping — including a
   regression test that a two-key `@oneOf` value is rejected, since the naive encoding accepts it
   silently.
3. **Type emission + golden tests.** TypedDicts, enums, fragments as composable TypedDicts, emitted
   from the real `operations.graphql`. Golden files under `tests_graphql_codegen/__resources__/`
   using `test-utils`' `snapshot` fixture. Prove the interface-chain case
   (`GetRoleMappingItem`, `operations.graphql:1717`) generates correctly **without** the repeated
   fragment spreads — then delete that workaround from the document.
4. **Runtime + client emission.** The pure core (`build_request` / `parse_response` over bytes),
   executor protocols, error types, the pre-serialised envelope per operation, and the emitted
   transforms. Sync client methods taking `variables` positionally with keyword-only `options`.
   Gate: the 59 transform-free operations emit a bare passthrough, and generating twice is
   byte-identical. `atoti-client` is not touched yet.
5. **`@required`.** Directive declaration, validation of placement, stripping before the wire, and
   the non-`Optional` annotation. Test that a null raises with the field path.
6. **Batching plugin.** Precomputed merge templates and the deferral-free typed `batch()` with its
   `@overload`s; port `_merge_documents` / `_merge_variables` / `_unmerge_output` / `_naming` from
   `atoti/_graphql/_batching/` with the AST code replaced by string handling, and add error
   attribution by alias suffix. `OperationBatcher` and `BatchedOperationFutureOutput` stay in
   `atoti-client` as its scheduling policy. Reuse the existing
   tests in `tests_atoti/graphql/test_operation_batcher.py` and `test_document_merging.py` as the
   behavioural contract.
7. **Async plugin.**
8. **End-to-end proof.** Generate the complete client from the real schema + operations; assert it
   type-checks under `ty`; exercise it against a fake in-process executor (no server, no HTTP).

---

## Remaining design decisions — all settled

Nothing here is left open. Recorded in full because each one shaped the design.

### Naming — settled: keep ariadne's scheme, no dedup

**Decision: path concatenation as ariadne does it. Long names are fine. No structural
deduplication.**

The decisive argument against dedup is stability, not size. Dedup makes a type's identity depend on
whether some _other_ selection elsewhere happens to match it — so adding one field to one selection
could rename types across the codebase. A name must be a pure function of (operation name, path),
which keeps it locally stable: adding a field renames nothing.

Long names cost far less than they look, because TypedDict call sites _subscript_ rather than
annotate — you write `output["dataModel"]["cube"]`, never
`GetLevelOrderingDataModelCube`. Measured over the 194 names hand-written code imports from
`_graphql`:

|                                 | count |
| ------------------------------- | ----- |
| enum / scalar / helper          | 117   |
| input types                     | 56    |
| fragment types                  | 13    |
| **long nested-selection types** | **8** |

Only 8 of 194 are path-concatenated, and the longest actually referenced is 55 characters, not the
117-character worst case that exists in the generated module. Four of those eight are the
`GetRoleMappingItem*Sso{Ldap,Oidc}Security` pairs used in `isinstance`, which become `__typename`
narrowing and stop being named at all.

What must stay stable is what code actually imports: fragment types, input types and enums — all
short and derived directly from schema or fragment names. Those are stable by construction.

(For reference, had dedup been pursued: identical shapes collapse 425 → 303, only 29%, and the wins
are trivial shapes — `{ __typename }` 34×, `{ status }` 18×, `{ name }` 15×.)

### Resolved

**Config is a Python module.** A `graphql_codegen_config.py` exporting a config object. Custom
scalars need _callables_, so real functions are referenced directly
(`parse=atoti._timestamp.parse_timestamp`) with no import-path strings to resolve, and `ty` checks
the config itself.

**Plugins stay** — struct is a third case alongside batching and async, which is enough to justify
the seam. Because config is a Python module, a plugin _is_ a config entry:
`plugins = [BatchingPlugin(), AsyncPlugin(), StructPlugin(interface="Struct")]`. No entry points, no
discovery, no extras. Conditional on the hook set staying small; sketch it against all three before
committing, and fall back to config booleans if it does not.

**Generated output stays gitignored.** PRs stay clean and there is no risk of a stale checked-in
client. Two consequences: the `atoti.__init__` bootstrap problem remains, and **runtime/generated
version compatibility stops being an issue** — output is always produced by the installed generator,
so it cannot go stale.

**Errors always raise.** Any `errors` array raises, matching today's behaviour exactly; partial data
is discarded. Keeps the happy path returning a plain typed dict and avoids `@required` interacting
confusingly with field errors.

**Batch errors fail only the affected futures.** Each error is attributed by its `path` prefix to the
operation that produced it; unaffected futures resolve normally. Matches GraphQL's independent root
fields. Requires attribution logic in `_unmerge_output`, which does not exist today.

**`@required` on a list asserts the list itself.** Element nullability comes from the schema's own
`!`. No new syntax; all 221 existing sites are on object fields, so nothing needs it today.

**`@deprecated` is ignored entirely** — treated exactly as if absent. Deprecation is a linter's
concern, not a code generator's.

**All lint/coverage exclusions on generated code stay.** ruff, pylint and coverage continue to skip
it. The guarantee moves: **the library's own tests are what prove generated clients are correct**,
rather than re-checking the output in every consuming repo. This makes the corpus and the `ty`-clean
assertion in Milestone 8 load-bearing rather than nice-to-have.

> **Confirmed, not assumed:** this is already how the repo works. `[tool.ty.src] exclude` lists the
> generated directory today, **71** modules under `atoti/` import from it, and `ty` is green — so
> exclusion suppresses _diagnostics for_ those files without removing their types from _import
> resolution_. See [B.5](#b5--a-risk-that-turned-out-not-to-be-one). What it does mean is that a bad
> emission can never be caught downstream, which is why the guarantee has to live in this library's
> tests.

### Determinism — declaration order, never set order

Golden files are the primary test, so emission order is part of the contract, not an
implementation detail. One rule: **everything is emitted in the order it is declared in the source
that defines it**, and nothing is ever iterated from a `set`.

| Emitted                                        | Order                                                                                         |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Operation modules / methods                    | order of appearance in `operations.graphql`                                                   |
| Nested selection TypedDicts                    | depth-first, in selection-set order, parent before children                                   |
| Fields within a TypedDict                      | selection-set order (schema field order for input types)                                      |
| Fragments                                      | order of appearance, emitted before first use                                                 |
| Enum members                                   | schema order, **not** sorted — GraphQL enum order is meaningful                               |
| Input types, scalars, shared struct transforms | first-use order within the above traversal                                                    |
| Schema files                                   | `sorted(glob)`, already done by `_generate_client.py:63` with its `# for determinism` comment |

Two guards, both cheap: a lint rule (or a plain code-review habit) against `set`/`frozenset`
iteration in `_emit/`, and a test that generates the full client twice in one process and asserts
byte-identical output — which also catches accidental `id()`-dependent ordering and dict reuse.
`dict` insertion order is guaranteed, so `dict.fromkeys(...)` is the dedup idiom, never `set`.

### Generator error reporting — graphql-core locations, no traceback

Every `Node` carries `node.loc` with `start`/`end` offsets and a `Source` whose `name` and
`location_offset` give the file. `graphql.get_location(source, position)` converts an offset to
`(line, column)`, and `graphql.print_source_location(source, location)` renders the familiar
caret-underlined excerpt for free. Two facts make this work end to end:

- `parse()` must be called **with** locations (the batching runtime uses `no_location=True`; the
  generator must not).
- Pass a real `Source(text, name=str(path))` per file rather than concatenating strings, or every
  error points at line _n_ of an anonymous blob. This is a change from `_print_merged_schema`'s
  `linesep.join(...)`, which is fine for the schema (errors there are ours, not the user's) but
  wrong for `operations.graphql`, which is user-authored.

So the generator raises a single `CodegenError` carrying a rendered message:

```
operations.graphql:1717:5: @required on `Query.dataModel` cannot be used on a non-null field.

1716 | query GetRoleMappingItem($cubeName: String!) {
1717 |     dataModel @required {
     |     ^
```

Applies to all generator-side diagnostics, not just directive misuse: an injected-variable type
inconsistency names all 35 declaration sites, an unknown `open_enums` member names the config line,
and a variable/parameter collision names the operation. Validation errors from
`graphql.validate()` already carry `locations` and `source`, so they are reformatted, not
re-derived. Errors are **collected and reported together** where they are independent — one run
should not surface one problem at a time.

### `atoti-client`'s executor

The executor is the whole of `atoti-client`'s transport surface, and today's generated client
already shows its exact shape: `plugin.py:52-70` injects an `__init__` that sets
`Accept: GRAPHQL_RESPONSE_MIME_TYPE`, posts to the relative url `"graphql"`, and wraps
`self.execute(...)` + `self.get_data(response)` into the `execute_operation` callback handed to
`OperationBatcher`. That callback **is** the executor, minus ariadne's base class.

```python
# atoti/_graphql/_executor.py — new, ~15 lines
def create_executor(http_client: httpx2.Client, /) -> Executor[None]:
    def execute(body: bytes, /, *, options: None = None) -> bytes:
        response = http_client.post(
            "graphql",
            content=body,
            headers={
                "Accept": GRAPHQL_RESPONSE_MIME_TYPE,
                "Content-Type": _types_map[".json"],
            },
        )
        response.raise_for_status()
        return response.content

    return execute
```

Four things it inherits for free, and one it must add:

- **Auth, TLS, trace-context injection and cookie stripping are already on the `httpx2.Client`**
  (`client/client.py:171-186` event hooks), so the executor does none of it.
- **HTTP error mapping is also already there** — `_enhance_json_response_raise_for_status`
  (`client/client.py:82`) rewrites `HTTPStatusError.args` from the server's error body. Calling
  `raise_for_status()` is the whole mapping.
- **`timeout=None` is deliberate** (`client/client.py:201`, "Do not change this") — the server
  manages timeouts. So `OptionsT` stays `None` here and the PEP 696 default applies; no
  `Httpx2Options`.
- **Gating is unchanged.** `Client._graphql_client` stays a `cached_property` returning `None` when
  `has_compatible_server_api` is false (`client/client.py:325`); only its body changes, to
  `GraphqlClient(executor=create_executor(self.http_client), injectors={...})`. The two injectors
  read the existing `_get_data_model_transaction_id` and its data-transaction counterpart.
- **New: non-JSON responses.** With a bytes boundary a proxy's HTML error page reaches
  `json.loads` instead of `httpx`'s `.json()`. `raise_for_status()` catches the common case; the
  runtime must still turn a decode failure into a clear `ProtocolError` quoting the first
  bytes, not a bare `JSONDecodeError`.

`OperationBatcher` keeps taking a callback of the same shape, so the ambient layer is untouched.

### CLI and build integration

Slots into the existing socket exactly. `atoti.js build` runs one task titled `GraphQL codegen`
(`javascript/cli/src/commands/build/command.ts:39-48`):

```ts
[
  "uv",
  "run",
  ...(coverage ? ["coverage", "run"] : ["python"]),
  "-m",
  "project.ariadne_codegen",
];
```

The migration replaces `project.ariadne_codegen` with `graphql_codegen`, whose `__main__.py` is:

```python
graphql_codegen --config <path>     # defaults to ./graphql_codegen_config.py
```

Config discovery, deliberately dumb: **an explicit `--config` path, or `graphql_codegen_config.py`
in the current directory.** No upward search, no `pyproject.toml` `[tool.*]` section, no
convention-over-configuration. The config is a Python module (decided above) so it is imported by
path with `importlib.util.spec_from_file_location` and must export a module-level `CONFIG`; `ty`
checks it as ordinary source.

Three details the port must carry over:

- **It must stay runnable under `coverage run`** — the CLI wraps it, and
  `report_unnecessary_coverage_pragmas` depends on that.
- **Repo-relative paths.** Today `_generate_client.py` resolves everything against
  `PROJECT_ROOT_DIRECTORY.parent` and reads the monorepo-root `graphql.config.json` for the schema
  glob and document paths. The new config declares `schema` and `documents` directly (as real
  `Path`s), and `project/` keeps a thin module that builds the config from `graphql.config.json` so
  the single source of truth for IDE tooling and the Java-side test documents does not fork.
- **Exit codes and output.** Non-zero on any diagnostic, the rendered errors on stderr, and nothing
  on stdout when it succeeds — so the CLI task's failure display is the generator's message.

During the coexistence window both generators can run: ariadne into `atoti/_graphql/client/`, the
new one into a scratch directory the tests read. Only the migration follow-up flips the build task.

## Current layout and naming (September 26, 2026)

This supersedes what the sections above say where they disagree.

### Its own repository (September 28, 2026)

- The library left the monorepo for `/Users/tibdex/work/graphql-codegen`, MIT licensed, with a fresh history: `python/graphql-codegen/` is gone, and `atoti-root` takes it from a local editable `path` source until it is published.
- Its own code checks with ruff and ty, on Python 3.12 to 3.15, and `tests_graphql_codegen/` is `tests/`.
- On 3.15, `typing_extensions.TypedDict` is no longer `typing`'s, which `typing.is_typeddict()` rejects: the tests declare their types through `runtime._compat`, as generated code does, and coverage skips both branches of a version check.
- The generated code is guaranteed for ty, pyright and pyrefly, each of which the tests run on the bookshop and on the adversarial package; mypy and zuban are dropped, since each needs an accommodation of its own.
- Standing alone showed it only ever ran on 3.12: `ast._Unparser`, private and gone in 3.14, gave way to respelling `ast.unparse()`'s string literals through `tokenize`, for a byte-identical output.

### The bookshop's operations sit next to their modules (September 30, 2026)

- `bookshop/operations/`, one operation per file, gave way to a `.graphql` file next to each module running its operations, as Atoti does: `quickstart.graphql` holds `GetOrder`, and `app.graphql` everything `app.py` runs, which now includes `ListBooks` and `GetPublication`, in `catalogue()` and `pages()`, rather than leaving them to the tests alone.
- `documents: "*.graphql"`, quoted since a YAML value starting with `*` is an alias, does not match the schema, `schema.graphqls`.

### A client is generic over its transport (September 30, 2026)

- A client's type is named after its transport's signature, written once as a `Protocol`: `Client = runtime.Client[Transport]`, so that a function taking a `Client` knows which arguments it may pass, keywords and defaults included, where `Client[...]` let it leave out a required one.
- Generic over the transport rather than over its parameters, since a `ParamSpec`'s value has no spelling with keyword parameters, and PEP 692 only unpacks a concrete `TypedDict`: each overload binds the parameters through its `self` annotation.
- A plain alias rather than a `type` statement, since only the former can be called to build a client, which checks the transport against the protocol.
- A positional `TypedDict` of options, `runtime.Client[[TransportOptions]]`, was checked too, but would make every call pass one, `{}` at least.
- Generated operation constants are annotated: pyrefly widens an inferred `Literal['mutation']` to `str` in a list comprehension passed to the clients' overloads, which `Client[...]` had hidden.

### A request may return its execution error (September 30, 2026)

- `request.returning_error()` makes a client return its `ExecutionError` in place of its data, typed `Data | ExecutionError[Data]`, so that `parse_data()` is typed by the request, which no `except` clause can say, and the union must be narrowed before the data is used.
- Raising stays the default, as in `gql` and Apollo Client's default error policy: a caller asks for the data, which a response with errors cannot give, and a forgotten check could not fail silently, as a forgotten `raise_for_status()` does with httpx.
- Always returning a result, with `data()` raising, was weighed and dropped: every call would unwrap, and a result nobody reads, a mutation's, would swallow its error, which Python has no `must_use` to catch.
- It is the request's choice rather than the client's, so that the existing overloads type it, each position of a merge on its own; a merge raises when one failing request raises, with every failure, and a merge whose data is null, like a request error, still raises, being no one request's.
- `ExecutionError` is generic in its parsed data, a mapping by default.

### Partial data is kept as received (September 30, 2026)

- `ExecutionError.data` is the partial data as the server sent it, and `parse_data()` converts a fresh deep copy like full data at each call, a method since it works and a codec may raise, uncached so that the caller may change its copy, the null of a `@nonNull` field moving up to its nearest nullable parent.
- Converting `data` in place dropped what the server sent next to a null `@nonNull` field, all of it when no parent was nullable, and left no way to see the raw data; a function to parse it on demand would have made a caller find the operation, which an `ExceptionGroup` of merged operations does not say.

### No `HttpError` (September 30, 2026)

- The runtime never raised it, and it was its one assumption that HTTP carries the requests: a transport now raises its own library's errors, as it already did for a timeout or a refused connection, and `ClientError` covers what the runtime raises.
- GraphQL over HTTP sends a request error as a GraphQL response with a 4xx status, so the bookshop's transports return the body of any `application/graphql-response+json` response, whatever its status, which the runtime raises as a `RequestError`.

### Only what a caller uses is public (October 1, 2026)

- The runtime exports the four clients, the errors with `Error` and `Location`, `OMITTED`, and `Operation` and `Request`, the types of what a caller holds, each from a module without a leading underscore: `client`, `error`, `injection` and `operation`.
- `Operation` and `Request` have nothing public but what a caller does with them, calling an operation and `returning_error()`, and `Injectors` is private, only the generated `injector.py` naming it; they are plain classes rather than dataclasses, so that their keywords stay public while what they hold is private, `Operation`'s `type` becoming `operation_type` so as not to shadow the builtin.
- The rest is the generated code's and the clients': `Codec` and `NON_NULL`, which generated code reaches through `runtime._reflection`, and the plumbing a client of one's own needs, as Atoti's mutation batcher does, from private modules that may change without notice; the runtime's modules reach each other's private members, which `SLF001` allows there alone.
- `schema/_enum.py` and `schema/_input.py` are `enum.py` and `input.py`, being public.
- A subject has one module, not a `_foo.py` beside a `foo.py`: a public module's private names start with an underscore, which the library's other modules import as they are; a module with nothing public, such as `_merge.py`, is private as a whole.
- The generator's public API is `generate()`, `Config`, and the settings it takes that are not plain values, `PackageLocation`, `Scalar`, `IdentityScalar` and `CodecScalar`: frozen dataclasses that check themselves on construction, from Python or from a configuration file, whose reader builds them through `scalar._parse()` and adds where it is as a note.
- A forward reference to a type named like a builtin, such as an input type `input` referenced before it is defined, reads as the builtin to pyrefly, a bug of its own to report: sorting input types by dependency would not fix it within a cycle, which still needs an alias, so `from __future__ import annotations` stays until Python 3.14 is the minimum, whose lazy annotations (PEP 649) make it needless for classes.

### Operations may sit next to their documents (October 1, 2026)

This replaces "No collocation" (September 29, 2026), which weighed collocation before graphql-config's projects, gitignored clients and lazy imports changed its costs.
The bookshop collocates with `{document}_graphql`, and Atoti with `_{document}_gql`, since it has a module of its own, `_udaf_graphql.py`, that a gitignore pattern for the other would catch.

- `documentModulePattern` (`document_module_pattern`), dotted and relative to a `.graphql` file's directory, generates that file's operations into one module next to it: `"_lol_{document}"`, or `"_generated._lol_{document}"` for a subpackage of the generator's own, which is optional.
  Left `null`, it is `{package}.operation.{document}`, as "The pattern places every module" (October 2, 2026) says.
- One module per `.graphql` file, never one per operation: `near-operation-file`'s `filePerOperation` is declined, since splitting the `.graphql` file gives as many modules, and the layout mirrors the user's own.
- `{document}` is the file's stem settled like any name, which is why it is not called a stem: a character no identifier holds becomes `_`, a leading digit gets one before it, an underscore the pattern puts next to it merges with the stem's own, so that `_cube_restrictions.graphql`'s module is `_cube_restrictions_gql`, and a keyword gets PEP 8's trailing one; two documents whose modules would be one file in a directory, case aside, are an error, since a suffix would let adding a document rename another's module, from which code imports types.
- What operations share stays in one package: the runtime, `schema`, `_scalar.py`, `injection.py`, and fragments, which are shared by definition. A collocated module imports only that package, so it is a leaf: nothing imports it but the user's code, and no import cycle is possible.
  Importing it still runs the `__init__.py` of every package around it, which may import back: Atoti's `atoti.cube` importing `atoti.distribution._query_cube_gql` ran `atoti/distribution/__init__.py`, which imports `atoti.cube`, so an operation two packages use goes next to the outer one's module, which is how dependencies go anyway.
- It imports that package absolutely, since a relative import cannot leave its top-level package, as atoti-client-ai's must: a runtime copy per distribution would not do, since its classes are nominal, as `isinstance(requests, Request)` shows.
  `directory` gives way to `moduleRoot`, where the distribution's imports start, as uv's build backend calls it, and `package`, the shared package's dotted path, validated part by part: the directory is computed from both, with no IO, which the other way round, finding where packages start from a path, would need.
- One project spans the documents of every distribution sharing the package, since `schema` only holds what some operation reaches.
- `generate()` stays pure, and its plain calls unchanged: collocation is two optional settings, `document_module_pattern` and `package_location`, holding `module_root` in the terms of the documents' source names and `package`, and every file is keyed by its path relative to the package's directory, a module next to a document by one walking up from it, `../app_graphql.py`.
- One module holds several operations, so an operation's types are settled within it, the operations keeping their names: `Get`'s data type is `GetData_1` next to an operation `GetData`.
- `operation/__init__.py` keeps re-exporting every operation, but lists them in `__lazy_modules__`, which Python 3.15 imports lazily (PEP 810) and earlier versions ignore, importing them eagerly as before: type checkers see plain imports, and `import atoti`'s 151 operation modules, 24 ms of their own, cost nothing until used.

### The pattern places every module (October 2, 2026)

- `documentModulePattern` always has a value, `{package}.operation.{document}` by default, so that one code path places every document's module: a leading `{package}` resolves it inside the package, in a subpackage of its own, any other pattern from the document's directory.
- One module per document in every layout, never one per operation, for the reasons "Operations may sit next to their documents" gives.
- A pattern inside the package must name a subpackage, which must not be one the package holds, case aside: `{package}.{document}` could clash with `runtime` or `schema`.
  Only its deepest subpackage re-exports, the others' `__init__.py` being empty.
- A module in the package steps aside for an operation another module defines, which the re-export would bind over it: `Get.graphql` holding `Other` gets `Get_1.py` next to `other.graphql` holding `Get`.
- `Collocation` becomes `PackageLocation(module_root, package)`, needed only by a pattern putting modules outside the package, since collocation is now just one value of the pattern.
  The setting stays `documentModulePattern`, not `documentModulePathPattern`: its value is a dotted module name, and `module_root` is a path because it is one, in the documents' source names' terms.
- `Config` checks the pattern, and `PackageLocation` its own `package`.

### The config checks itself (October 2, 2026)

- `generate(**args: Unpack[_Params])`, `_Params` being a closed `TypedDict` of the document, the schema and a `Config`, private as the explicit signature it replaces had no public type either, every one required, so that whatever builds them, as the configuration reader does, fails type checking until it supplies a parameter added later, which a parameter with a default would let it miss; each parameter is documented under its key rather than repeated in an `Args:` section, and `Config()` stands for the defaults. A frozen dataclass declares each setting's default once, on its field, and checks on construction what needs neither the document nor the schema, the document module pattern, the package location it may need and the non-null directive's name.
- The configuration reader builds the `Config` as it parses the file, before any glob is read, so that its errors are located by a note, and `Project` holds it rather than repeating each setting.
- The document and the schema stay out: they are known only once the files are read, and checking them is generating, which `generate()` does next anyway; a constructor checking them would raise one line earlier, and `replace()` would check them again.
- `non_null_directive_name` stays `None` unless named, unlike the pattern: a default name would fail on a schema declaring a directive of that name, a first run's error about a feature its user never asked for, and a name no document uses is harmless, a misspelled one already failing validation as an unknown directive.
- What needs them is checked by `PackageEmitter`, which `generate()` only calls: what can fail is derived on construction, so that no emitter holds a schema, a document or options it cannot generate from, the schema checked and declaring the client directive, its struct interface and the scalars checked against it, the document validated, the operations' locations checked when their modules go next to them, and the injections found; what cannot fail is derived on first use, in `cached_property`s.
- `Config` has a module of its own, `config.py`, which both `generate.py` and the emitter import.

### Each part has one home (October 3, 2026)

- One `DataTypeEmitter` serves the whole package, a `cached_property` of `PackageEmitter`, so that each fragment is normalized once, whichever modules spread it: how a module refers to a fragment, by name in a sibling fragment's module or through `_fragment` elsewhere, is an argument of `emit()`.
- `_metadata.py` holds the distribution's metadata and the `GENERATED` marker, so that the command and the configuration reader import no emitter.
- The command writes every generated file over whatever is at its path and touches nothing else, leaving only a file already holding its content: users keep their code under version control, which reverts an overwrite, and gitignore the generated files, among which a module left behind by a deleted document is harmless, since nothing generated imports it. Checking a directory before replacing it, removing stale files and listing the modules next to documents in the package's `__init__.py` for that were dropped as more machinery than they were worth.
- A document's module is named after its document alone, which makes clashes errors rather than suffixes: two documents whose modules would be one file, case aside, a document named `__init__`, and, in the package, a module named like an operation another document defines, which the re-exporting `__init__.py` would hide. Naming can therefore fail, so `PackageEmitter` names the modules on construction.
- The runtime's `_prepare.py` prepares requests and reads responses, a lone one's and a merge's, so that `operation.py` holds only `Operation` and `Request`, and `client.py` only the clients; an operation gives its own request body, next to its envelope, and a lone request's `ExecutionError` is built once, with its operation's parser.
- A check says what a value should be, never where its caller found it, and the caller says where with `_note.error_note()`, which adds a note to any `Exception` raised inside, a note never changing what is raised: a scalar notes the field whose path it checks, and the configuration reader reads each key with `_required()` or `_optional()`, which pop it, parse its value and note the key, so that an error in a nested key gets one note per level, innermost first, `In `parse`.` up to `In project `shop`.`. Every problem raises as found: one at a time is fine, with no `where` to thread through every function.
- The fragments' types share one module, `fragment.py`, ordered by `graphlib.TopologicalSorter` so that a fragment follows those it spreads: a module per fragment needed module names spelled apart from each other, sibling imports for bases and a re-exporting `__init__.py`, and the types reach each other by name.
- `DataTypeEmitter` caches its normalized fragments and the keys each provides with `functools.cache` wrappers it holds, and `operation_spellings()` settles each operation's variables and data types with `settle_names()`, the operations' own spellings taken.
- `check()` knows what the package holds besides the documents' modules, so that `Config` knows nothing of the package's layout, and the default pattern is built from `OPERATION_PACKAGE`.

### The command's parts are reusable (September 29, 2026)

- `_graphql_config.read_graphql_config(path, extension_name=...)` reads a graphql-config file into the `_Params` of `generate()` for each project, by the directory its package goes to, and `_write.write(directory, files)` writes each: every package is generated before any is written, and each is reported as it is, so that a failure part way shows which ones were; `_cli.main()` chains reading, generating and writing, so that the reader stops at `generate()`'s arguments and knows nothing of generating.
- Reading is the only IO: `read_graphql_config()` loads the file with the loader `_get_loader(path)` picks, `parse_graphql_config(config, extension_name=...)` turns the loaded value alone into `Project`s, their globs and `Config`, by directory relative to the configuration's, and `parse_project(project, schema=..., documents=...)` turns the files those globs matched into `generate()`'s arguments, which the configuration cannot give alone since those files are read in between.
- They are internal, like everything but `generate()`, so that power users may use them knowing they may change without notice.

### No `--project` (September 28, 2026)

- The command generates every project with the extension: generating is quick and deterministic, so narrowing to one saved nothing, and no caller used it.

### Errors are Python's and graphql-core's (September 28, 2026)

- `generate()` raises the first problem it finds: a `ValueError` for a setting whose value is wrong, and a `GraphQLError`, located at its nodes, for a problem in the document or the schema; graphql-core's validation of either reports every error it finds, which `generate()` raises in an `ExceptionGroup`, so that `except* ValueError` and `except* GraphQLError`, which catch a bare error too, handle each apart.
- Problems the generator finds itself are raised as found rather than gathered, a group of one being complexity for nothing: a second problem shows on the next run.
- The command lets every problem raise as the library does, traceback included; only invalid arguments exit, with status 2, as argparse does.
- `CodegenError`, `Diagnostic` and `_diagnostics.py` are gone: they predate problems being exceptions.
- graphql-core's `Source.get_location()` puts a node starting a line at the end of the one before, since it counts lines with `str.splitlines()`; its own validation errors already live with it, and so do ours now, until graphql-core fixes it.

### `__typename` is written, not added (September 28, 2026)

- A selection branching by type must select `__typename`, unaliased and unconditional: a graphql-core validation rule, run with the spec's rules in one traversal and assuming nothing they check, reports every one missing, where it is.
- It branches exactly where its type is generated as a union: a field's or a fragment's selection on an abstract type with more than one possible type, through an inline fragment, or a spread conditional or on a narrower type.
- Nothing adds it any more: the document sent, and the types, are the one written, which Apollo Client and Relay's compiler rewrite only because their normalized stores need it.

### Atoti generates through the command

- `EXTENDED_AT_RUNTIME_PLACEHOLDER` is gone from the server's schema, its Java removal visitor and ESLint's exemption: the two `@oneOf` inputs it filled are declared without fields, which the spec's grammar allows, and always extended by at least one plugin, which graphql-java's validation then accepts.
- Atoti's settings live in the monorepo's `graphql.config.yml`, and its build runs `python -m graphql_codegen ../graphql.config.yml`, which writes exactly what `project.graphql_codegen` wrote, now deleted.

### Documentation

- The README is the only documentation: no guides.
- Every code block in it is a file of `bookshop/`, checked verbatim by `test_readme.py`.
- It links to the tests for the exact rules, rather than restating them.
- It explains why responses are typed rather than validated: the server already guarantees their shape, so only static types add safety.

### Runtime modules

- `_operation`: `Operation`, `Request`, `PreparedRequest`, and the functions taking them: `resolve_variables()`, `prepare()`, `parse_data()`, `execution_error()`, `parse_response()`.
- Dataclasses declare fields and properties only, cached or not: what takes arguments besides the instance is a function, so data stays data.
  `Operation.__call__` is the one exception, since it is what makes `GetBook({...})` a request.
- `_merge.encode_body()` encodes the body of one request or several merged, which is why it is not named after merging.
- `_injection`: `OMITTED`, `Injector`, `Injectors`.
- `_client`: the four clients.
- `_merge`, `_reflection`, `_error`, `_transport`, `_compat`, as before.
- `PACKAGE_DOCSTRING` marks a generated package, which is what lets the command replace it.

### Dunders and dataclasses

- The clients are plain classes, with neither `__eq__` nor `__repr__`: they perform side effects, so comparing or printing one is never useful.
- `Operation` leaves its document and types out of its repr, which names the operation: `Operation(type='query', name='GetBook')`.
- `Request` and `PreparedRequest`, created by every call, are slotted.
- `PreparedRequest.__bytes__` is its body, its only accessor: the object is what goes on the wire.
- `REQUIRED` is a PEP 661 sentinel, like `OMITTED`.
- No `__or__` to merge operations: a tuple is typed position by position, which an operator chain would not be.
- No context manager on the clients: they own no resource, the transport does.

### Generator modules

- Flat: every generator module sits directly in `graphql_codegen`, next to `runtime`, with no `codegen` or `_emit` subpackage.
- `_package` assembles every module through one `_finish()`, which adds a module's helper imports for the subpackage it sits in and settles its pending spellings.
- `_struct` holds the struct convention, `_injector` the injector module, and `_document` validation, `__typename` insertion and the transitive fragment closure.
- An anonymous operation is rejected by validation, with a located diagnostic.
- `_selection` infers no type: a `TypeInfo`, driven by the recursive descent through its `enter()` and `leave()`, gives each field's definition and each selection set's type, and `is_type_sub_type_of()` tells whether a fragment's type condition holds.
  It only follows spreads and reads `@skip` and `@include`, which graphql-core leaves to its caller.
- Only graphql-core's public API is used, `graphql.__all__`: not turms's `graphql.utilities.type_info.get_field_def`, which graphql-core does not export and 3.3 replaces.
- The resolved model carries graphql-core's types rather than their names, and a leaf field's selection set is `None`, as in graphql-core's AST.
- It requires graphql-core 3.3, whose AST nodes are frozen dataclasses, rebuilt with `dataclasses.replace()`; a directive's missing arguments or directives may be `None`, and an input field's default sits in `default_value` when given as a Python value and in `default` when given as a GraphQL literal.
- `as_non_null()` is generic over graphql-core 3.3's `Nullable | GraphQLNonNull[Nullable]` aliases, which needs no cast.
- The non-null directive is a `GraphQLDirective`, added through `GraphQLSchema.to_kwargs()`, so that graphql-core checks its name.
- One pass writes a merge template: it inlines what cannot keep its shape in a merge, aliases root fields and suffixes variables, while a document that cannot be merged, a subscription's or one with directives of its own, is printed as is.
  Every document escapes a `§` in its strings, so that no `§` left is anything but a sigil.
- The sigil is not a setting: a GraphQL name cannot hold a `§`, a string escapes it and printing drops comments, so no document can collide with it.
- Pending spellings are settled in creation order, so helper aliases, created at import, come first without a flag.
- A `Key` carries whether it is required, and the TypedDict emitters say so: explicitly in a closed TypedDict, only when it may be absent in an open one.

### Naming

- No `GraphQL` prefix in the public API, since GraphQL is implicit: `ClientError`, `ResponseError`, `RequestError`, `ExecutionError`, `ProtocolError`, `RequiredFieldError` and `CodegenError`.
- The spec's terms wherever it has one: an entry of `errors` is an `Error`, an entry of its `locations` a `Location`, and a field's alias or name its response name (`response_name`).
- A resolved selection set is a `SelectionSet`, held by a field or a type condition as `selection_set`.
- `Operation.parse_data` converts a response's data in place; there is no public `Transform` alias.
- The client's assertion that a nullable field is not null for an operation speaks the spec's vocabulary, where Non-Null is a value never null and required an argument or input field that must be provided: `non_null_directive_name` (`nonNullDirectiveName`), the `NON_NULL` marker, `UnexpectedNullError`, and `@nonNull` in the bookshop and in Atoti.
  Relay's `@required` stays available, the directive's name being a setting.
- A *name* is what GraphQL, or an emitter, would like, and a *spelling* how generated code writes it: `*_name` always holds the former, and `*_spelling` the latter.
  `Spelling` is a `SettledSpelling`, decided, or a `PendingSpelling`, invented and decided once its module is complete; `settle_names()` settles names known up front, `settle_module()` a module's pending spellings, and `ast_str()` gives the `str` an AST holds for either.
  Compilers call a token's exact text its spelling, as Clang's `getSpelling()` does; what is settled late is what Lisp calls a `gensym`, and CPython, needing none, hides its own names behind a dot, as `.0`.

### Tests

Organized by concern, each rule a named case of a parametrized table:

- `test_emit.py`: how selections become types, and what the package imports.
- `test_generate.py`: what each option of `generate()` changes, and every input it rejects, each problem reported once.
- `test_naming.py`: names never clash.
- `test_cli.py`: the command, end to end on the bookshop, its one argument leaving nothing else to test there.
- `test_graphql_config.py`: reading a configuration, its errors on plain dicts, and files only where reading them is the point.
- `test_write.py`: writing a package.
- `test_merge.py`, `test_reflection.py`, `test_runtime.py`, `test_client.py`, `test_error.py`: the runtime.
- `test_bookshop.py`: the README's example runs as it says.
- `test_readme.py`: the README shows only files.

Each part is tested where it is, as a unit, rather than only through `generate()` or the command, which leaves one end-to-end test each; generation tests use the bookshop schema unless a case needs a schema of its own.

## Test corpus — hand-rolled, plus a Hypothesis fuzzer

**Decision: author both the schemas and the operations ourselves.** Vendoring public schemas
(APIs-guru, GitHub, Shopify) was considered and dropped — it is the wrong tool for this generator.

The reason is structural: **public APIs publish _schemas_, not _operations_.** Almost everything
interesting here is driven by operations — fragment spreads, inline fragments, aliases, `@required`
placement, variable defaults, what gets batched — so a corpus of real schemas gives type-system
diversity and nothing else. And the features that carry the most risk have **no public corpus at
all**, because they are ours or too new: `@required`, injected variables, batching, the `Struct`
convention, and `@oneOf`. Vendoring also drags in a licensing question for a library intended for
publication (several schemas are covered by API terms, not an OSS licence) and snapshots that go
stale. Hand-rolled schemas cost little — they are a few dozen lines each — and every one exists to
pin a specific behaviour.

Scale and type-system breadth, which is the one thing public schemas would have given, comes from
the fuzzer instead, over generated schemas we control. Two layers:

### Layer 1 — an authored conformance suite (this is what drives coverage)

One directory per feature under `tests_graphql_codegen/__resources__/`, each holding a minimal
`schema.graphql`, `operations.graphql`, the golden generated output, and a sample response with its
expected transformed result. Cases, drawn from everything settled in this plan:

|                |                                                                                              |
| -------------- | -------------------------------------------------------------------------------------------- |
| scalars        | custom scalar leaf, in a list, nested in a struct, in an input, round-trip                   |
| nullability    | `@required` at depth, on a list, on a list element, inside an inline fragment                |
| enums          | closed, open with the sentinel, open+closed pair for a both-position enum                    |
| inputs         | `@oneOf`, recursive (`*Condition`), defaults (non-null-with-default), `NotRequired` omission |
| composition    | fragment spread, several spreads, nested fragments, fragment on an interface                 |
| abstract types | interface, interface-implements-interface chain, union, `__typename` narrowing               |
| structure      | nested lists, list of objects, aliases, `__typename`, a reserved-word field name             |
| operations     | variables with defaults, injected variables, mutation batching, `on_output`                  |
| failure        | null at a `@required` path, unknown enum member, a `@oneOf` with two keys                    |

### Layer 2 — a Hypothesis fuzzer over generated schemas and operations

Hypothesis is a **dev-only test dependency**, so it does not touch the dependency budget. Three
composable strategies, each producing a graphql-core AST rather than text (so nothing has to be
re-parsed and every value is valid by construction):

1. **`schemas()`** — build a `GraphQLSchema` with scalars, enums, input objects (including
   recursive ones and `@oneOf`), objects, interfaces with `implements` chains, unions, and list /
   non-null wrapping at every nesting depth. Draw sizes small by default; Hypothesis widens them
   when shrinking a failure.
2. **`operations(schema)`** — walk a drawn schema and emit valid selection sets: bounded depth,
   aliases, fragment definitions and spreads, inline fragments on interfaces and unions, variables
   with and without defaults, and directive placement.
3. **`responses(schema, operation)`** — the inverse walk: synthesise a well-formed response for a
   given operation, with nulls at nullable positions and unknown members for open enums.

Then assert as properties:

| property          | asserts                                                                             |
| ----------------- | ----------------------------------------------------------------------------------- |
| **generates**     | no diagnostic for a valid document; `compile()` accepts the module                  |
| **type-checks**   | `ty check` is clean on the generated module                                         |
| **round-trips**   | `parse_response(op, build(responses(...)))` equals the input modulo scalar coercion |
| **deterministic** | generating twice in one process is byte-identical (the guard above)                 |
| **rejects**       | an invalid document produces a located diagnostic, never a traceback                |

The `ty` property is the expensive one — a subprocess per example — so it runs on a reduced example
count in CI and a larger one nightly, with any failure's shrunk schema + operations pinned into
layer 1 as a permanent regression case. That is the intended pipeline: **the fuzzer finds cases, the
conformance suite keeps them.**

One thing the fuzzer must generate deliberately rather than by luck, since it is the whole
correctness risk: **paths needing a transform**, i.e. custom scalars and `@required` buried under
lists, inline fragments and recursive inputs. Weight the strategies so those appear often, and
assert the transform ran — the `typing.is_typeddict` bug below is precisely the failure a
generates-and-compiles property would have missed.

### One caveat on the coverage goal

100% line coverage of the generator is reachable with layer 1, but coverage cannot see this
generator's real risk: **emitting code that runs fine and is wrong**. Every case must therefore assert
on _output_ — golden file, `ty` clean, and a runtime round-trip — not merely execute the path. The
reflection design sharpens this: the `typing.is_typeddict` bug found while prototyping it produced a
transform that ran, raised its `@required` errors correctly, and silently coerced nothing. It would
have passed any coverage-only test.

## Verification

Golden-file testing follows the repo's own idiom — `test-utils`' `snapshot` fixture compares files
_and directories_, reports failures as a real `git diff`, and refreshes with `--update-snapshots`.
Tests live in `tests_graphql_codegen/`, marked `serverless` so they run without server JARs.
`hypothesis` is a dev-only test dependency (the fuzzer above); its `ty`-checking property runs at a
reduced example count in CI and a larger one nightly.

```bash
cd atoti-python-sdk
uv run pytest -k graphql_codegen              # unit + golden tests
uv run pytest -m serverless                   # full fast lane
uv run ty check                               # incl. the generated output
uv run ruff check python/ && uv run ruff format --check python/
uv run python -m pylint python                # undeclared-dependency, final-class, …
pnpm run atoti test --only serverless         # everything CI runs
```

Milestone 8 is the real proof: generating the complete 102-query / 50-mutation client from the
unmodified production schema, type-checking it, and driving it through a fake executor.

---

## Deferred to the migration follow-up

Not in this plan, but sized here so the follow-up is not a surprise:

- ~60 modules under `atoti/` rewrite attribute access → subscripts
  (`output.data_model.cube.dimension.hierarchy.description` →
  `output["dataModel"]["cube"]["dimension"]["hierarchy"]["description"]` — note the keys become
  camelCase, since there is no case conversion).
  Evaluate a LibCST codemod: the generator knows the types, so most of it should be mechanical.
- Input construction sites, including the `Callable[[SomeInput], None]` mutate-in-place pattern
  (`hierarchy.py:840`).
- Every `_to_graphql` / `_from_graphql` helper on identifier classes.
- Delete `_graphql/_batching/_merge_documents.py`, `_merge_variables.py`, `_unmerge_output.py` and
  `_naming.py` (the four that move into the library), `_empty.py`, and the whole
  `project/src/project/ariadne_codegen/` tree. `operation_batcher.py` and
  `batched_operation_future_output.py` **stay** — they are Atoti's scheduling policy, load-bearing
  for `connected_session_client.py:48` and `flush_prematurely()`.
- Add `atoti/_graphql/_executor.py` (~15 lines, sketched above) and change
  `Client._graphql_client`'s body (`client/client.py:325`) to construct the new client with that
  executor and the two injectors.
- Flip the build task in `javascript/cli/src/commands/build/command.ts:45` from
  `project.ariadne_codegen` to `graphql_codegen`.
- Doctests asserting on generated exception paths — `column.py:110`, `session.py:317`,
  `tables.py:541` all name `atoti._graphql.client.exceptions.*`.
- The committed API snapshot `atoti/__resources__/api.json`.
- `graphql.config.json` at the monorepo root gains the `@required` declaration so IDE tooling and
  the Java-side test documents keep validating.

The generated output stays **gitignored** (decided above), so there is no committed-client
regeneration check and no staleness risk; the bootstrap problem — `atoti` cannot be imported until
codegen has run — persists exactly as today.

---

# Appendix A — Provenance: what was measured

Every figure used above, in one place, with what produced it. All against the real schema
(21 `.graphqls` files, 347 types) and the real 2009-line `operations.graphql`, unless stated.

## A.1 — Feasibility probes (the "can this even work" questions)

| Question                                                                                                                      | Answer                                              | How                                                                                                                                                                                                                                                                                                                                              |
| ----------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Can `graphql-core`'s `TypeInfo` replace ariadne's own type inference?                                                         | **Yes, entirely**                                   | Merged schema builds; **all 174 operations validate with 0 errors** once client directives are declared. A `TypeInfoVisitor` walk of `GetHierarchyDescription` yields parent type, resolved type _with_ wrappers (`String!`, `[Table!]!`, `Cube`), directives and argument definitions at every level                                            |
| Does it handle interface chains (ariadne [#254](https://github.com/mirumee/ariadne-codegen/issues/254), open since Dec 2023)? | **Yes**                                             | `... on OidcSecurity` narrows correctly in `GetRoleMappingItem`; `get_possible_types` resolves `SsoSecurityWithRoleMapping → [LdapSecurity, OidcSecurity]` and `Table → [ExternalTable, InMemoryTable]`                                                                                                                                          |
| Does `graphql-core` support `@oneOf`?                                                                                         | **Only ≥3.2.12, so we do not rely on it**           | 3.2.6 (the repo's pin) rejects the schema with `Unknown directive '@oneOf'`; on 3.2.12 it builds unaided and `is_one_of` is `True` on all **20** inputs. We declare the directive ourselves and read one-of-ness off the AST, which gives the same **20** on both versions (verified equal to `is_one_of` on 3.2.12)                              |
| Does the `Struct` convention hold mechanically?                                                                               | **Yes**                                             | `schema.is_sub_type(Struct, t)` finds all **9** `*Struct` implementors; `removesuffix("Struct")` resolves each to a real input type. Replaces the `add_support_for_struct_types.py` monkeypatch                                                                                                                                                  |
| Is `closed=True` (PEP 728) available?                                                                                         | **Yes, already**                                    | `typing_extensions==4.15.0` is the repo's existing pin and supports `closed` + `extra_items`; `ty` understands it                                                                                                                                                                                                                                |
| Does `closed=True` survive inheritance?                                                                                       | **Yes**                                             | `__closed__` is `True` on the subclass and an extra key is still `extra_forbidden`; single and multiple inheritance both merge keys; `ty` sees inherited keys                                                                                                                                                                                    |
| Does `__typename` work in class syntax?                                                                                       | **No — mangled**                                    | `class X(TypedDict): __typename: str` silently becomes `_X__typename`; a real payload fails with _both_ `missing ('_X__typename',)` and `extra_forbidden ('__typename',)`. Fixed by a one-line functional base                                                                                                                                   |
| Does `Literal` `__typename` narrow?                                                                                           | **Yes**                                             | `ty` narrows on `==`, narrows in `match`, type-checks `case _ as never: assert_never(never)`, and reports `invalid-key` on the wrong branch                                                                                                                                                                                                      |
| Do recursive input types work?                                                                                                | **Yes**                                             | Forward-reference strings (`NotRequired[list["Cond"]]`) work in both pydantic and `ty`, and `ty` catches a wrong element type inside the recursive list                                                                                                                                                                                          |
| Is the typed `batch()` expressible?                                                                                           | **Yes, via `@overload`**                            | Heterogeneous gives exact per-position types (`tuple[DeleteTableOutput, CreateMeasureOutput]`); homogeneous with runtime-`n` gives `tuple[DeleteTableOutput, ...]` — the `asyncio.gather` idiom                                                                                                                                                  |
| Can the runtime reach **zero** dependencies?                                                                                  | **Yes, but rejected**                               | Verified working: generated TypedDicts behind `TYPE_CHECKING` + `from __future__ import annotations`, client importing with `typing_extensions` blocked entirely, `ty` still checking fully. Given up when reflection was chosen — see Appendix B.2                                                                                              |
| Can we reuse graphql-core's error types at runtime?                                                                           | **Only the wire shape, which is the spec's anyway** | Read `graphql/error/graphql_error.py`: `GraphQLFormattedError` is a `TypedDict, total=False` of `{message, locations, path, extensions}` — spec §7.1.2, ~10 lines to re-declare. `GraphQLError` the class adds `nodes`, `source`, `positions`, `original_error` — **4 of 8 attributes are server-side AST/execution state** a client cannot fill |
| Does a new `python/*` directory need a workspace-list edit?                                                                   | **No**                                              | `[tool.uv.workspace] members = ["python/*"]`                                                                                                                                                                                                                                                                                                     |
| Does starting private skip the dependency-version tests?                                                                      | **Yes**                                             | `python/project/tests_project/conftest.py:22` filters with `if is_published(package_name)`, so `test_synced_requirements` and `test_minimum_version_installed` both skip it                                                                                                                                                                      |
| Can `graphql-core` be bumped to 3.2.12 at all?                                                                                | **No — it breaks ariadne-codegen**                  | It resolves and installs, then ariadne-codegen 0.16.0 crashes with `TypeError: Redefinition of reserved type 'String'` (it synthesises `GraphQLScalarType(name="String")` for `__typename`; 3.2.12 forbids redefining reserved types). 0.19.0 fixes it, but upgrading a generator we delete is wasted risk. Pin stays at 3.2.6                   |

## A.2 — Schema and document census (the numbers that shaped decisions)

| Measured                                               | Count                                       | What it decided                                                                                       |
| ------------------------------------------------------ | ------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `@mixin(from: ".mixins", import: "NonNullable")` sites | **221** (47 chars each)                     | The motivating problem                                                                                |
| ↳ lookups where the caller knows the name exists       | 116 (52%)                                   | → Relay `@required`, **not** Apollo `@semanticNonNull`                                                |
| ↳ auth / config / plugin gated                         | 24 (11%)                                    | ″                                                                                                     |
| ↳ genuinely never null                                 | 81 (37%)                                    | ″                                                                                                     |
| Input fields with a default                            | **15** — and **all 15 are non-null**        | The rule `Required` ⟺ non-null **and** undefaulted. Reading `!` as required would be wrong            |
| Field arguments with a default                         | 0                                           | Baked into the document; invisible in Python                                                          |
| Variable definitions with a default                    | 0                                           | Nothing to handle today; the rule is still written                                                    |
| Enums                                                  | **41**                                      |                                                                                                       |
| ↳ input-position only                                  | 30                                          | Openness is meaningless for these                                                                     |
| ↳ output-position only                                 | 4                                           | Openness applies                                                                                      |
| ↳ both positions                                       | 7                                           | Need **two aliases** when open (open for output, closed for input)                                    |
| Recursive input types                                  | **25** (the `*Condition` family)            | Forward refs are mandatory, not optional                                                              |
| `@oneOf` inputs                                        | **20**                                      | Union of single-key `closed=True` TypedDicts                                                          |
| `@deprecated` uses                                     | **0**                                       | Ignored entirely — a linter's concern                                                                 |
| Operations (SDK / incl. Java test docs)                | **152 / 174**                               | 102 queries, 50 mutations                                                                             |
| Nested selection types needing names                   | **393**                                     | Naming scheme must scale                                                                              |
| ↳ longest ariadne-style name                           | 117 chars                                   | Looks alarming; see next two rows                                                                     |
| Names hand-written code imports from `_graphql`        | **195**                                     | The set that must stay stable                                                                         |
| ↳ enum / scalar / helper                               | 117                                         | Short, schema-derived, stable                                                                         |
| ↳ input types                                          | 56                                          | ″                                                                                                     |
| ↳ fragment types                                       | 13                                          | ″                                                                                                     |
| ↳ **long nested-selection names**                      | **8**, longest actually referenced 55 chars | Long names cost almost nothing → **no dedup**                                                         |
| Operations needing **no transform at all**             | **59 of 152 (39%)**                         | Pure passthrough; zero runtime cost                                                                   |
| Median transform sites per operation                   | **1**                                       | The emitted traversal stays small                                                                     |
| Worst case (`GetLevelOrdering`)                        | 5 `@required` + 1 struct, 7 levels deep     | 7 flat statements                                                                                     |
| `$dataModelTransactionId` in operations                | **160** uses, **80** operations             | → injected variables, config-named                                                                    |
| `dataModelTransactionId` as an input field             | **35** input types                          | A directive would mean 115 new annotations → rejected                                                 |
| `dataTransactionId` as an input field                  | 1                                           | ″                                                                                                     |
| Nominal-typing sites that must be rewritten            | **12**                                      | TypedDicts are structural                                                                             |
| ↳ `isinstance()` (all in `security/role_mapping.py`)   | 6                                           | → `__typename` narrowing, strictly better                                                             |
| ↳ class patterns (`_identification/*_identifier.py`)   | 6                                           | → split into two functions; `ty` does **not** narrow TypedDict unions on `in`                         |
| `with mutation_batcher.batch():` sites                 | **16**, all co-located                      | All map onto the deferral-free core `batch()`                                                         |
| `batch` in `atoti/__resources__/api.json`              | **0**                                       | Batching is **not** public API                                                                        |
| Dedup potential, had it been pursued                   | 425 → 303 shapes (29%)                      | Wins are trivial shapes: `{__typename}` 34×, `{status}` 18×, `{name}` 15× → not worth the instability |

## A.3 — Benchmarks

Two payloads throughout: **small** = 5 rows × 4 fields, **large** = 100 rows × 20 fields.

**Response handling** — the decisive comparison:

| Approach                                              | small      | large        | vs. pydantic  |
| ----------------------------------------------------- | ---------- | ------------ | ------------- |
| pydantic `BaseModel` validate                         | 19.8 µs    | 1321 µs      | 0.5x          |
| pydantic TypedDict validate                           | 12.6 µs    | 679 µs       | 1x (baseline) |
| pydantic + `SkipValidation` everywhere unneeded       | 6.8 µs     | 109 µs       | 6.2x          |
| generic walker over an emitted plan                   | 6.8 µs     | 84 µs        | 8.2x          |
| reflection-driven walker, paths hoisted to build time | —          | 62 µs        | 11x           |
| **inlined generated code**                            | **3.2 µs** | **39–47 µs** | **~15–18x**   |

**Why interpreted Python beats pydantic's Rust core**, which is counter-intuitive enough to state
plainly: on the large payload pydantic performs ~2,200 leaf validations (every key at every level,
plus `closed=True` extra-key checks) while the transform touches **102 sites**. ~20x less work,
~18x faster — the ratio _is_ the explanation. If full structural validation were actually needed,
pydantic would win comfortably. It is redundant only because static typing already covers it.

**Request building** (bytes boundary + pre-serialised envelope):

|                                       | median op (270 B) | largest op (751 B) |
| ------------------------------------- | ----------------- | ------------------ |
| dict envelope, re-serialised per call | 4.02 µs           | 5.89 µs            |
| pre-serialised envelope               | **2.39 µs**       | **2.44 µs**        |

The number is small; the _shape_ is the point — request building becomes **flat in document size**.

**TypedDict vs. `BaseModel`**, independent of validation strategy:

|                          | TypedDict | BaseModel | ratio |
| ------------------------ | --------- | --------- | ----- |
| Validation, small        | 12.6 µs   | 19.8 µs   | 1.57x |
| Validation, large        | 679 µs    | 1321 µs   | 1.95x |
| Read a 5-deep path       | 117 ns    | 238 ns    | 2.03x |
| Memory, one large result | 529 KiB   | 1134 KiB  | 2.14x |

Attribute access is **slower** than subscript (pydantic reads go through `__dict__` machinery) —
the opposite of most people's intuition, and worth knowing when the migration's subscript rewrite
gets questioned.

**Measured and discarded:** dropping aliases / case conversion saves only **~3%** (473 → 460 µs).
So "no case conversion" is a simplicity decision, not a performance one — do not defend it on speed.

## A.4 — Honest limits of these numbers

- 0.64 ms on a large response is **noise against a 10–100 ms round trip**. Performance is a
  _secondary_ argument behind sans-io and correctness. It matters most for memory on large held
  result sets and for high-throughput batching.
- The `BaseModel` comparison is against a _plain_ model. Ariadne's generated models carry aliases
  and config that would widen the gap, so treat 2x as indicative, not a precise claim about ariadne.
- All benchmarks are synthetic payloads shaped like the real ones, not captured production traffic.

---

# Appendix B — Design reversals and prototype traps

## B.1 — Why this section exists

Seven times the design was wrong and something corrected it. Each reversal is recorded with its
_cause_, because the cause generalises and the conclusion alone does not. Anyone continuing this
work should read these before re-opening a settled decision — several are attractive-looking ideas
that were tried and failed for non-obvious reasons.

## B.2 — The seven reversals

**1. "Fragments must be expanded explicitly."** Wrong. Challenged with "can't we rely on TypedDict
inheritance like ariadne-codegen does". Verified: a functional base for `__typename` plus class
syntax gives inheritance, `closed=True` survives it, multiple inheritance merges keys, and `ty` sees
inherited keys. → **Fragment spread = base class.** No expansion, no duplication.

**2. "Zero runtime dependencies."** True for the inlined + `TYPE_CHECKING` design (verified
working), but **false once reflection was chosen**: reflection reads `__annotations__` at runtime, so
the types must exist at runtime, and they are declared with PEP 728 `closed=` — the one thing stdlib
`typing` lacks. Dropping `closed=` is not an option since it is what makes `ty` reject a two-key
`@oneOf`. → **`typing_extensions` is a runtime dependency**, accepted as an extension of the stdlib.
The zero-dependency route is recorded above as measured and working, should it ever matter more.

**3. Calling struct "an Atoti invention."** Corrected: it is the
[GraphQL Struct RFC](https://rfcs.graphql.org/rfcs/Struct/). Verified status — **RFC 0 / Strawman,
no champion**, last updated November 2023. → Support it as a **plugin**, so any project using the
same interface workaround benefits, and so `struct` support has a home if the RFC ever lands. Do not
over-lean on it: it is a recognised gap with a written-up proposal, not an imminent standard.

**4. Calling `flush_prematurely()` "a hack that disappears."** Wrong — it is the _ambient escape
hatch_. Verified it is a **no-op unless an outer batch is already open** (because `submit()` already
wraps itself in `with self.batch():`), so `udaf_measure.py:208` calling it is positive evidence that
it runs nested inside someone else's scope.

**5. Proposing a batch facade.** Rejected on the grounds that it breaks _client-controlled
batching_ — "the method implementations don't know in advance how they will be used" — and therefore
breaks composition through SDK layers. → Reverted to the ambient `ContextVar`, and the final design
splits mechanism (pure `merge`/`split`, library) from policy (scheduling, `atoti-client`).

**6. Over-weighting subscripts-vs-attributes as "the biggest adoption barrier."** Corrected:
sacrificing runtime performance for that DX delta is a bad trade, and `ty` autocompletes TypedDict
keys in VS Code anyway. A.3 then showed attribute access is _slower_. → TypedDicts, no hesitation.

**7. Misattributing the caching win.** The build was already cached per operation; the actual win was
**hoisting path strings to build time** (2.7x → 1.29x vs. inlined). Worth flagging as a general
lesson: attribute a speedup to the change that produced it, or the wrong thing gets kept.

## B.3 — Four prototype traps

All four were hit while building throwaway prototypes. All four would survive a naive test suite.

**1. `typing.is_typeddict()` returns `False` for `typing_extensions.TypedDict` subclasses.** This is
the dangerous one. It made the entire reflective transform a **silent no-op**: it ran, it raised its
`@required` errors correctly, and it **coerced nothing**. It was caught only by diffing its output
against the inlined implementation's. → Use `typing_extensions.is_typeddict`, and — more
importantly — **every test must assert on transformed output**, never merely that the code ran. This
is why the test-corpus section insists on golden file + `ty` clean + runtime round-trip per case,
and why 100% coverage is explicitly called insufficient.

**2. `get_type_hints(td, include_extras=True)` does _not_ strip `Required[...]`.** The reflective
builder must unwrap `Required`/`NotRequired` itself before matching on the inner type, or every
required field is misclassified.

**3. `ty` rejects keyword splats in functional TypedDicts.** `TypedDict("X", {**Frag.__annotations__,
...})` works at runtime but `ty` errors with _"Keyword splats are not allowed in the `fields`
parameter to `TypedDict()`"_ and then treats the result as having **no keys**, so every access
becomes `invalid-key`. The DRY-looking composition is statically invisible. → Use inheritance.

**4. A functional TypedDict's string name must match its variable name**, or `ty` raises
`mismatched-type-name`. Mechanical, but it bites the emitter on the one construct that cannot use
class syntax.

## B.4 — Environment notes for whoever picks this up

- The repo's `uv` is pinned `>=0.11.16` while the sandbox had `0.8.17`; prototypes were run in a
  scratch venv to work around it.
- `ty` needs `--python <venv>` to resolve pydantic in a scratch venv.
- Do not benchmark inside a `Visitor` subclass without calling `super().__init__()` — graphql-core
  visitors fail obscurely otherwise.

## B.5 — A risk that turned out not to be one

An earlier draft flagged this as the thing to retire before building anything:

> With the generated directory in `[tool.ty.src] exclude`, confirm `atoti-client`'s call sites still
> resolve types from it — if exclusion removes them from resolution, the static safety story
> collapses, and that story is the entire justification for having no runtime validation.

**It is already the setup today, and it works.** Checked:

- `pyproject.toml` `[tool.ty.src] exclude` contains
  `python/atoti-client/src/atoti/_graphql/client`, commented `# Autogenerated.`
- **71 modules** under `atoti/` import from it (`from .._graphql.client import JoinClusterInput`, …)
- `atoti-python-sdk/.gitignore:34` confirms the directory is generated, not committed

If exclusion removed those types from resolution, all 71 modules would be unresolved-import errors
and `uv run ty check` would be red. It is not. The semantic is the ordinary one shared with mypy and
pyright: **`exclude` governs which files get diagnostics reported, not which files are available for
import resolution.** Excluded modules are still parsed, and their types resolved, when an included
file imports them.

**The real consequence — and it is a design constraint, not a risk.** Errors _inside_ the generated
code are never reported in the consuming repo. Nobody downstream will ever catch a bad emission.
That is exactly why the settled decision to keep all lint/coverage exclusions comes paired with
moving the guarantee: **the library's own tests are what prove generated clients are correct.** It
makes the conformance corpus and milestone 8's `ty`-clean assertion load-bearing rather than
nice-to-have, and it is the reason the fuzzer runs `ty` as a property rather than only compiling.

**Not re-verified in this session**, for two reasons that are worth knowing before trying: the
generated directory does not exist in a fresh checkout (gitignored; codegen has not run), and the
sandbox `uv` was `0.8.17` against the workspace's required `>=0.11.16`.

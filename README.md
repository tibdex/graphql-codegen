# graphql-codegen

[![PyPI](https://img.shields.io/pypi/v/graphql-codegen?style=flat-square)](https://pypi.org/project/graphql-codegen)
[![Python](https://img.shields.io/pypi/pyversions/graphql-codegen?style=flat-square)](https://pypi.org/project/graphql-codegen)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen?style=flat-square)](https://github.com/tibdex/graphql-codegen/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](https://github.com/tibdex/graphql-codegen/blob/main/LICENSE)

This library turns a GraphQL schema and your operations into a typed Python package with:

- no imposed transport: bytes in, bytes out;
- no runtime dependencies[^typing-extensions]: nothing to install, nothing to upgrade;
- no runtime validation: just dicts, statically checked.

## Installation

```shell
pip install graphql-codegen
```

Or, with `uv`:

```shell
uv add --dev graphql-codegen
```

The generator's only dependency is [graphql-core](https://github.com/graphql-python/graphql-core)[^typing-extensions].

## Quick start

Every example of this README is a file of [`bookshop`](https://github.com/tibdex/graphql-codegen/tree/main/bookshop), which uses [`schema.graphqls`](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/schema.graphqls):

<!-- excerpt: bookshop/schema.graphqls -->

```graphql
type Query {
  # …
  "The order with this identifier."
  order(id: ID!): Order
  # …
}

enum OrderStatus {
  PENDING
  SHIPPED
  DELIVERED
  CANCELED
}

type Order {
  id: ID!
  status: OrderStatus!
  # …
}
```

Write your operations in `.graphql` files:

<!-- file: bookshop/get_order.graphql -->

```graphql
query GetOrder($id: ID!) {
  order(id: $id) {
    id
    status
  }
}
```

The generator reads [graphql-config](https://the-guild.dev/graphql/config), the file GraphQL editor extensions and linters already use[^bootstrapping]:

<!-- excerpt: bookshop/graphql.config.yml -->

```yaml
# An SDL file here, but globs, introspection results, and server URLs work too.
schema: schema.graphqls
documents: "*.graphql"
# This library's config, under its extension name.
extensions:
  pythonCodegen:
    # Where imports start, relative to this file's directory.
    moduleRoot: ..
    # Dotted name from the module root, so written to `bookshop/graphql`.
    package: bookshop.graphql
```

Generate the package[^yaml]:

```shell
graphql-codegen bookshop/graphql.config.yml
```

The generated package:

- depends on nothing but the standard library[^typing-extensions];
- holds only the `enum` and `input` types your operations reach, so that it grows with your documents rather than with the schema;
- puts each document's operations in a module of its own.

Your type checker verifies every variable and every field of the response.
The example below shows what the type checker can prove with [`assert_type()`](https://docs.python.org/3/library/typing.html#typing.assert_type) and [`assert_never()`](https://docs.python.org/3/library/typing.html#typing.assert_never):

<!-- file: bookshop/quickstart.py -->

```python
from typing import Literal, assert_never, assert_type

from bookshop.get_order_graphql import GetOrder
from bookshop.graphql.schema import OrderStatus
from bookshop.transport import send

data = send(GetOrder({"id": "o1"}))
order = data["order"]

# `Query.order`'s type is nullable, so the type checker requires this check.
if order is None:
    print("No such order.")
else:
    # `Order.id: ID!` is a `str` on the wire.
    assert_type(order["id"], str)

    # An `enum` gets a generated alias of the `Literal` of its values.
    assert_type(order["status"], OrderStatus)
    assert_type(order["status"], Literal["PENDING", "SHIPPED", "DELIVERED", "CANCELED"])

    if "total" in order:
        # A field not selected in the GraphQL operation can never be there.
        assert_never(order)

    print(f"Order {order['id']} is {order['status']}.")
```

> [!NOTE]
> The `send()` function above is the transport you write: see [Sans-IO](#sans-io).

## Types

### Type checking over runtime validation

GraphQL is strongly typed, and the server:

- [validates](https://spec.graphql.org/September2025/#sec-Validation) each operation against its schema before running it;
- [responds](https://spec.graphql.org/September2025/#sec-Response) with exactly the operation's shape.

That leaves a client little to validate[^breaking-changes].
What a Python client still lacks is knowing, while you write `order["status"]`, that the key exists and holds an `OrderStatus`.
That is a type checker's job, done once, before the code runs.

This library therefore generates exact types for your type checker and leaves each response as decoded from JSON.
Only [custom scalars](#custom-scalars) with a codec are converted, and only fields asserted [non-null](#non-null-fields) are checked.

> [!NOTE]
> Most other Python codegen libraries rely on [Pydantic](https://docs.pydantic.dev) models for both type checking and runtime validation.
> The [TypeScript GraphQL Code Generator](https://the-guild.dev/graphql/codegen) (the reference for generating code from GraphQL) could validate each response with [Zod](https://zod.dev) but relies on type checking alone.
> This library makes the same call.

### Operation types

Each operation gets a type for its variables and one for its data, both keyed by the names the GraphQL document uses.
Wherever GraphQL lets a value be one of several things, its Python type is a union:

- a selection on a [`union`](https://spec.graphql.org/September2025/#sec-Unions) or an [`interface`](https://spec.graphql.org/September2025/#sec-Interfaces) is one of several types, and becomes one type per concrete type, told apart by [`__typename`](https://spec.graphql.org/September2025/#sec-Type-Name-Introspection) (which the generator selects for you);
- an [`enum`](https://spec.graphql.org/September2025/#sec-Enums) is one of several values, and becomes a [`Literal`](https://docs.python.org/3/library/typing.html#typing.Literal);
- a [`@oneOf` `input`](https://spec.graphql.org/September2025/#sec-OneOf-Input-Objects) is one of several fields, and becomes a union of single-key types, so that a value with two keys fails type checking.

These unions are closed for the type checker, but nothing enforces them at runtime, since responses are [not validated](#type-checking-over-runtime-validation).

A server may add a `type` to a `union`, an implementation to an `interface`, a member to an `enum`, or a field to a [struct](#structs)'s `input` type at any time, since none of these is a breaking change.
A validating client would reject the new value before your code gets a chance to see it.
This library hands it to your code as sent, and leaves the choice to you.

One way is to accept the unknown value in a `case _:` arm:

<!-- excerpt: bookshop/app.py -->

```python
def status_label(status: OrderStatus, /) -> str:
    match status:
        case "PENDING":
            return "Being prepared"
        case "SHIPPED":
            return "On its way"
        case "DELIVERED":
            return "Delivered"
        case "CANCELED":
            return "Canceled"
        case _:
            # A member added after codegen.
            return status.replace("_", " ").capitalize()
```

The other way is to reject it with `case _ as never: assert_never(never)`, which:

- raises an `AssertionError` at runtime;
- makes your type checker point at every missing `case` next time you run the codegen.

For instance, [`app.graphql`](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/app.graphql) selects a publication's length, in pages when it is printed and in minutes when it is an audiobook:

<!-- excerpt: bookshop/app.graphql -->

```graphql
query GetPublication($id: ID!) {
  publication(id: $id) {
    title
    ... on Printed {
      pages: pageCount
      # …
    }
    ... on Audiobook {
      duration
    }
  }
}
```

`publication` is then a union of one [`TypedDict`](https://docs.python.org/3/library/typing.html#typing.TypedDict) per concrete type: `Audiobook`, and each `type` that `implements Printed`.
Checking for a key narrows it to the `TypedDict`s with that key, and also covers any new `type` that `implements Printed`.

`length()` then narrows it to one `TypedDict` with a `match` on `__typename`, rejecting any other with `assert_never()`:

<!-- excerpt: bookshop/app.py -->

```python
def length(publication_id: str, /) -> str:
    data = send(GetPublication({"id": publication_id}))
    publication = data["publication"]

    if publication is None:
        return "No such publication."

    if "pages" in publication:
        return f"{publication['title']} has {publication['pages']} pages."

    match publication["__typename"]:
        case "Audiobook":
            return f"{publication['title']} lasts {publication['duration']} minutes."
        case _ as never:
            assert_never(never)
```

### No name clashes

Nothing prevents a schema or a document from using names that clash with Python keywords (`class`, `from`), standard library names the generated code uses (`list`, `Sequence`), or the generator's own helpers.

Since `TypedDict` keys are strings, a field named `from` can stay the key `"from"`.
Only the names the generator adds, such as `_builtins` or `_GetBookData_book`, are chosen to avoid every other name in their module, so none can shadow another.

## Runtime

### Sans-IO

The small [sans-IO](https://sans-io.readthedocs.io) runtime is copied into the generated package, and its [public API](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/graphql/runtime/__init__.py) is limited to:

<!-- excerpt: bookshop/graphql/runtime/__init__.py -->

```python
from .error import (
    Error as Error,
    ExecutionError as ExecutionError,
    GraphqlError as GraphqlError,
    Location as Location,
    ProtocolError as ProtocolError,
    RequestError as RequestError,
    ResponseError as ResponseError,
    UnexpectedNullError as UnexpectedNullError,
)
from .injection import OMITTED as OMITTED
from .merge import merge as merge
from .operation import Operation as Operation, OperationRequest as OperationRequest
from .request import PreparedRequest as PreparedRequest, Request as Request
```

The generated package does no IO:

- `Request.prepare()` returns a `PreparedRequest`;
- `PreparedRequest.__bytes__()` returns the request body to send;
- `PreparedRequest.parse()` returns the response body as typed data.

You own what happens in between: any HTTP client (synchronous, asynchronous, or callback-based), or anything else that carries `bytes`.

This library ships no transport on purpose.
IO is a slippery slope: an HTTP client to pick, then headers, authentication, timeouts, and retries, each answered differently by every codebase.
The only winning move is not to play.

Writing a transport is short, and its few lines do not grow as you add operations.

For example, the bookshop's asynchronous transport uses [httpx2](https://pydantic.dev/docs/httpx2) and accepts a `timeout` (and nothing else):

<!-- excerpt: bookshop/async_transport.py -->

```python
GRAPHQL_RESPONSE: Final = "application/graphql-response+json"
http: Final = httpx2.AsyncClient(
    base_url="https://bookshop.example",
    headers={
        "Accept": GRAPHQL_RESPONSE,
        "Content-Type": "application/json",
    },
)


async def send[Data](
    request: Request[Literal["query", "mutation"], Data],
    /,
    *,
    timeout: float | None = None,
) -> Data:
    prepared = request.prepare(injectors=INJECTORS)
    response = await http.post("/graphql", content=bytes(prepared), timeout=timeout)

    # GraphQL over HTTP sends a request error as a response with a 4xx status.
    if not response.headers.get("Content-Type", "").startswith(GRAPHQL_RESPONSE):
        response.raise_for_status()

    return prepared.parse(response.content)
```

> [!NOTE]
> The `injectors=INJECTORS` above is only needed if you opt into [injectors (explained below)](#injectors).
> By default, `Request.prepare()` takes no argument.

`Data` goes from the request to the result, so each call is typed by its operation:

<!-- excerpt: bookshop/async_app.py -->

```python
async def title(isbn: ISBN, /) -> str:
    data = await send(GetBook({"lookup": {"isbn": isbn}}), timeout=5.0)
    assert_type(data, GetBookData)
    return data["book"]["title"]
```

[`transport.py`](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/transport.py) does the same with the standard library alone.

### Subscriptions

A subscription's response is a stream of bodies, one per event, and `PreparedRequest.parse()` reads each as it comes.
The bookshop's `subscribe()` reads [Server-Sent Events](https://github.com/graphql/graphql-over-http/blob/main/rfcs/GraphQLOverSSE.md); [`graphql-transport-ws`](https://github.com/graphql/graphql-over-http/blob/main/rfcs/GraphQLOverWebSocket.md) or [multipart HTTP](https://github.com/graphql/graphql-over-http/blob/main/rfcs/IncrementalDelivery.md) would be read the same way:

<!-- excerpt: bookshop/async_transport.py -->

```python
async def subscribe[Data](
    request: Request[Literal["subscription"], Data], /
) -> AsyncGenerator[Data, None]:
    prepared = request.prepare(injectors=INJECTORS)

    # Closing the generator leaves the block, closes the connection, and ends the subscription.
    async with http.sse("/graphql", method="POST", content=bytes(prepared)) as events:
        async for event in events:
            if event.event == "complete":
                return

            if event.event == "next":
                yield prepared.parse(event.data.encode())
```

Leaving the `aclosing()` block unsubscribes:

<!-- excerpt: bookshop/async_app.py -->

```python
async def watch(order_id: str, /) -> list[OrderStatus]:
    statuses: list[OrderStatus] = []
    events = subscribe(OnOrderStatusChanged({"orderId": order_id}))

    async with aclosing(events):
        async for event in events:
            statuses.append(event["orderStatusChanged"]["status"])

            if statuses[-1] == "DELIVERED":
                break

    return statuses
```

### Merging

Sometimes you only know at runtime which operations go together, and writing every combination as its own operation in a `.graphql` file is impractical.
Combining several operations into one request can also let the server answer faster, seeing the whole picture instead of independent requests asking for overlapping data.
Some other codegen libraries let you build operations at runtime for this, giving up type safety.
This library instead merges operations written ahead of time into one request, so that each result keeps its exact type.

`merge()` takes several queries, or several mutations:

<!-- excerpt: bookshop/app.py -->

```python
def book_and_similar(isbn: ISBN, text: str, /) -> tuple[str, list[str]]:
    # Two queries in one request.
    book_data, search_data = send(
        merge(GetBook({"lookup": {"isbn": isbn}}), Search({"text": text}))
    )
    assert_type(book_data, GetBookData)
    assert_type(search_data, SearchData)
```

A list built at runtime can be merged too, whatever its length.
Its results then share one type, the union of its operations' data types:

<!-- excerpt: bookshop/app.py -->

```python
def look_up(
    isbns: Sequence[ISBN], publication_ids: Sequence[str], /
) -> tuple[GetBookData | GetPublicationData, ...]:
    # Any number of queries in one request.
    return send(  # ty: ignore[unsound-return-statement]  # Pyright and Pyrefly already infer this.
        merge(
            [
                *(GetBook({"lookup": {"isbn": isbn}}) for isbn in isbns),
                *(GetPublication({"id": id_}) for id_ in publication_ids),
            ]
        )
    )
```

### Errors

A response with errors raises a `RequestError` when the request failed before execution, and an `ExecutionError` when it carries partial data:

<!-- excerpt: bookshop/graphql/runtime/error.py -->

```python
class ExecutionError(ResponseError, Generic[_Data_co]):
    """The server raised errors executing the request, but sent the rest of the data.

    A field that raised is `null`, as is its nearest nullable parent if it is non-null.
    """

    data: Final[Mapping[str, object] | None]

    def parse_data(self) -> _Data_co | None:
        """Return a fresh copy of the data, converted as in a response without errors.
```

When several operations are merged, an `ExceptionGroup` holds one for each operation that fails.

You can also get the error as a value: `returning_error()` gives a request for which `PreparedRequest.parse()` returns its `ExecutionError` instead of raising it:

- the result is then typed as either the data or an `ExecutionError`, so you can tell them apart with `isinstance()`;
- in a [merge](#merging), you choose for each request whether its error is returned or raised;
- a subscription carries on past an event with errors.

<!-- excerpt: bookshop/app.py -->

```python
def track(order_id: str, /) -> str:
    result = send(GetOrder({"id": order_id}).returning_error())

    if isinstance(result, ExecutionError):
        data = result.parse_data()
        assert_type(data, GetOrderData | None)
        return f"Partially loaded: {data} ({result!s})."

    tracked = result["order"]
    return (
        "No such order."
        if tracked is None
        else f"Order {tracked['id']} is {tracked['status']}."
    )


def cancel(order_ids: list[str], /) -> list[str]:
    results = send(
        merge(
            [
                CancelOrder({"input": {"order": order_id}}).returning_error()
                for order_id in order_ids
            ]
        )
    )
    # Each error has a note naming its operation and the variables sent.
    return [
        f"{error.__notes__[0]} {error!s}"
        for error in results
        if isinstance(error, ExecutionError)
    ]
```

## Config

### Custom scalars

[Custom scalars](https://spec.graphql.org/September2025/#sec-Scalars.Custom-Scalars) travel as JSON values in a format decided by the server, such as a date as an ISO 8601 string.
This library lets you give each one a Python type, with a codec converting its values when they differ from their JSON form:

<!-- excerpt: bookshop/graphql.config.yml -->

```yaml
extensions:
  pythonCodegen:
    # …
    scalars:
      DateTime:
        type: datetime.datetime
        codec:
          decode: ..scalar.decode_datetime
          encode: ..scalar.encode_datetime
      ISBN:
        type: ..scalar.ISBN
      Money:
        type: decimal.Decimal
        codec:
          decode: decimal.Decimal
          encode: str
      UUID:
        type: uuid.UUID
        codec:
          decode: uuid.UUID
          encode: str
```

Dotted names in the config resolve as follows:

- a bare name, such as `str`, is a builtin;
- an absolute dotted name, such as `decimal.Decimal`, is imported as is;
- a path starting with `..`, such as `..scalar.decode_datetime`, is relative to the generated package's directory.

A dotted name must be a module's attribute, so a method such as `datetime.fromisoformat` needs to be wrapped in a function:

<!-- excerpt: bookshop/scalar.py -->

```python
from datetime import datetime
# …


def decode_datetime(value: str, /) -> datetime:
    return datetime.fromisoformat(value)


def encode_datetime(value: datetime, /) -> str:
    return value.isoformat()
```

> [!TIP]
> If your project already relies on a validation library, it can supply the codec.
> For instance, with Pydantic, build `adapter = TypeAdapter(YourType)` once, then use `decode = adapter.validate_python` and `encode = partial(adapter.dump_python, mode="json")`.

`Request.prepare()` then encodes each scalar's values, and `PreparedRequest.parse()` decodes them, so your code only ever handles their Python types:

<!-- excerpt: bookshop/app.py -->

```python
def cheaper_than(limit: Decimal, /) -> list[str]:
    data = send(ListBooks({"filter": {"priceBelow": limit}}))
    labels: list[str] = []

    for book in data["books"]:
        price = book["price"]
        assert_type(price, Decimal)
        labels.append(f"{book['title']}: {price:.2f}")

    return labels
```

### Non-null fields

Schemas often make fields nullable, such as a lookup that may find nothing.
Yet you may know more than the schema, such as that a lookup will succeed, or want your code to fail fast on a `null` without writing `if value is None: raise …` at every use.
This library brings the idea of [Client Controlled Nullability](https://github.com/graphql/graphql-wg/blob/main/rfcs/ClientControlledNullability.md) to any server through a client directive:

<!-- excerpt: bookshop/graphql.config.yml -->

```yaml
extensions:
  pythonCodegen:
    # …
    nonNullDirectiveName: nonNull
```

With `@nonNull` on `book`, its type loses `| None`:

<!-- excerpt: bookshop/app.graphql -->

```graphql
query GetBook(
  "An identifier or an ISBN."
  $lookup: BookLookup!
  # …
) {
  book(lookup: $lookup) @nonNull {
    ...BookCard
    # …
  }
}

fragment BookCard on Book {
  # …
  author {
    name
  }
}
```

<!-- excerpt: bookshop/app.py -->

```python
def describe(isbn: ISBN, /) -> str:
    variables: GetBookVariables = {"lookup": {"isbn": isbn}}

    # Caught only to show what the error carries.
    # You would usually let the error propagate since a null here is a bug.
    try:
        data = send(GetBook(variables))
    except UnexpectedNullError as error:
        assert error.path == ["book"]
        assert error.__notes__ == [f"Raised by `GetBook` with variables {variables!r}."]
        return "No such book."

    # No `None` check: `@nonNull` took `| None` out of the type.
    book = data["book"]

    # `@nonNull` only covers `book`, so `author` may still be `None`.
    author = book["author"]
    by = "an anthology" if author is None else f"by {author['name']}"
```

> [!NOTE]
> On an [`ExecutionError`](#errors), `data` keeps the partial data as the server sent it, while `parse_data()` moves an asserted field's null up to its nearest nullable parent rather than raising, as the server does for a non-null field.

### Structs

A selection set has a fixed depth, so data of unbounded depth, such as a tree, can only come back as a JSON scalar.
The [Struct RFC](https://github.com/graphql/graphql-wg/blob/main/rfcs/Struct.md) proposes a new `struct` keyword: selected without a selection set, a field of a struct type returns its value whole, like a scalar.
This library brings that idea to today's servers with a convention, without waiting for the new keyword:

> - each `type` that `implements` a designated `interface` carries that scalar in the interface's single field;
> - that field's payload is typed by the `input` the `type` is named after, even a recursive one.

For instance:

<!-- excerpt: bookshop/graphql.config.yml -->

```yaml
extensions:
  pythonCodegen:
    # …
    structInterfaceName: Struct
```

<!-- excerpt: bookshop/schema.graphqls -->

```graphql
type Query {
  # …
  "The filter of the saved search with this name, exactly as it was saved."
  savedSearch(name: String!): BookFilterStruct
}

"A JSON payload with the shape of the input type the implementation is named after."
interface Struct {
  value: JSON
}

type BookFilterStruct implements Struct {
  value: JSON
}

"A recursive filter on books."
input BookFilter @oneOf {
  genre: Genre
  author: ID
  priceBelow: Money
  and: [BookFilter!]
  or: [BookFilter!]
  not: BookFilter
}
```

One query receives a `BookFilter` as data:

<!-- excerpt: bookshop/app.graphql -->

```graphql
query GetSavedSearch($name: String!) {
  savedSearch(name: $name) {
    value
  }
}
```

Another takes a `BookFilter` as a variable:

<!-- excerpt: bookshop/app.graphql -->

```graphql
query ListBooks($filter: BookFilter, $first: Int) {
  books(filter: $filter, first: $first) {
    ...BookCard
    genre
  }
}
```

So the same `BookFilter` goes from one to the other as is:

<!-- excerpt: bookshop/app.py -->

```python
def run_saved_search(name: str, /) -> list[str]:
    data = send(GetSavedSearch({"name": name}))
    search = data["savedSearch"]

    if search is None:
        return []

    book_filter = search["value"]
    assert_type(book_filter, BookFilter | None)
    # Sent back as is.
    books = send(ListBooks({"filter": book_filter}))
    return [book["title"] for book in books["books"]]
```

### Injectors

Some concerns are better centralized than spread over every call.

For instance, an idempotency key makes retrying a mutation safe:

1. the connection drops after the server placed an order;
2. the transport sends the order again;
3. the key, unique to the order, tells the server it already placed it, so it does not charge the customer twice.

GraphQL has no built-in idempotency, so the key is an argument or an input field of each mutation needing one, and getting hold of a key becomes every caller's concern.
The same goes for a database transaction ID.

This library handles such a value in one place instead, with an injector given to `Request.prepare()`.
It writes the value to every variable or input field with the name given in the config.
No variables' type accepts it, so that no caller can pass one by mistake:

<!-- excerpt: bookshop/graphql.config.yml -->

```yaml
extensions:
  pythonCodegen:
    # …
    injectorNames: [idempotencyKey]
```

`PlaceOrderInput` holds one, for instance:

<!-- excerpt: bookshop/schema.graphqls -->

```graphql
input PlaceOrderInput {
  "Makes placing the same order twice harmless: the client sends a new one per order."
  idempotencyKey: UUID!
```

The generated package's [`injection.py`](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/graphql/injection.py) module types the injectors you must supply, one function per name.
Each returns the value as its scalar's Python type, or `OMITTED` to leave a nullable one out.
`transport.py` builds them once, and gives them to `Request.prepare()` on each call:

<!-- excerpt: bookshop/transport.py -->

```python
INJECTORS: Final = injectors({"idempotencyKey": uuid4})
# …
    prepared = request.prepare(injectors=INJECTORS)
```

And no call can pass a key:

<!-- excerpt: bookshop/app.py -->

```python
def order(book_id: str, address: Address, /) -> str:
    data = send(
        PlaceOrder(
            {"input": {"lines": [{"book": book_id}], "shippingAddress": address}}
        )
    )
```

`Request.prepare()` calls the injectors once, so the key is in `bytes(prepared)`.
`transport.py` resends that body when the connection drops:

<!-- excerpt: bookshop/transport.py -->

```python
ATTEMPTS: Final = 3
# …
    prepared = request.prepare(injectors=INJECTORS)
    # Built once, so that a retry sends the same idempotency key.
    request_body = bytes(prepared)
    attempts_left = ATTEMPTS
    # …
    while True:
        try:
            response = _post(
                "/graphql",
                request_body,
                accept="application/graphql-response+json",
                timeout=timeout,
            )
        # …
        except ConnectionError:
            attempts_left -= 1

            if not attempts_left:
                raise

            continue
```

### Colocation

Most other codegen libraries gather every operation in one generated module, often inside a single class.
A feature's operations then live away from its `.graphql` files and the code calling them.
A project split into several packages cannot have each package own its operations either.
This library generates each operation as a module-level constant, so it can be colocated with both the document it comes from and the code calling it:

<!-- excerpt: bookshop/graphql.config.yml -->

```yaml
extensions:
  pythonCodegen:
    # …
    documentSiblingModule: "{document}_graphql"
```

This pattern puts `get_order.graphql`'s operations and fragments in `get_order_graphql.py`.
Code calling these operations imports them from there:

<!-- excerpt: bookshop/quickstart.py -->

```python
from bookshop.get_order_graphql import GetOrder
from bookshop.graphql.schema import OrderStatus
```

> [!NOTE]
> When the config does not set `documentSiblingModule`, each document's operations and fragments go into a module of the generated package's `document` subpackage instead, such as `document/get_order.py` for `get_order.graphql`.
> The subpackage re-exports them all, [lazily](https://docs.python.org/3.15/reference/simple_stmts.html#compatibility-via-lazy-modules) from Python 3.15, so that a module is only imported when one of its operations or fragments is used.

## Python API

The [public API](https://github.com/tibdex/graphql-codegen/blob/main/src/graphql_codegen/__init__.py) is limited to:

<!-- file: src/graphql_codegen/__init__.py -->

```python
from graphql_codegen.config import Config as Config
from graphql_codegen.document_sibling_module import (
    DocumentSiblingModule as DocumentSiblingModule,
)
from graphql_codegen.generate import generate as generate
from graphql_codegen.package_location import PackageLocation as PackageLocation
from graphql_codegen.scalar import Codec as Codec, Scalar as Scalar
```

[`generate()`](https://github.com/tibdex/graphql-codegen/blob/main/src/graphql_codegen/generate.py) is what the command calls:

<!-- excerpt: src/graphql_codegen/generate.py -->

```python
class _Params(TypedDict, closed=True):
    document: DocumentNode
    schema: GraphQLSchema
    config: Config


def generate(**args: Unpack[_Params]) -> dict[PurePosixPath, bytes]:
    """Pure function returning the content of each file of the generated package by its path."""
```

[^typing-extensions]: Before Python 3.15, the generated package needs [`typing-extensions`](https://github.com/python/typing_extensions) to access the latest [`typing`](https://docs.python.org/3/library/typing.html) features.
This library depends on it, but the package copied into your project does not inherit that, so add `typing-extensions` to your own dependencies too.

[^bootstrapping]: This library is partly [bootstrapped](https://en.wikipedia.org/wiki/Bootstrapping_(compilers)): to fetch a schema from a URL, [it uses an operation](https://github.com/tibdex/graphql-codegen/blob/main/src/graphql_codegen/_cli/_introspection.py) it generated itself from [`_introspection.graphql`](https://github.com/tibdex/graphql-codegen/blob/main/src/graphql_codegen/_cli/_introspection.graphql).

[^yaml]: A YAML config, like the quick start's, needs the `yaml` extra: install `graphql-codegen[yaml]` instead.
JSON and TOML configs need nothing more.

[^breaking-changes]: Well-behaved GraphQL APIs [avoid breaking changes](https://graphql.org/learn/schema-design/#versioning), adding fields and [deprecating](https://spec.graphql.org/September2025/#sec--deprecated) old ones instead.
Most breaking changes made after codegen never reach the code anyway: removing or renaming a field, changing its arguments, or turning its type from an object into a leaf or the reverse invalidates the operation, so the server rejects it, and `PreparedRequest.parse()` raises a [`RequestError`](#errors).
The few left would fail client-side validation as soon as the response arrives, but otherwise propagate deeper in your code, possibly unnoticed: a field becoming nullable, its leaf type changing, or it switching between a list and a single value.
The more often you rerun the codegen against the deployed schema, the sooner your type checker catches such a change.

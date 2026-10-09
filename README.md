# graphql-codegen

[![PyPI](https://img.shields.io/pypi/v/graphql-codegen?style=flat-square)](https://pypi.org/project/graphql-codegen)
[![Python](https://img.shields.io/pypi/pyversions/graphql-codegen?style=flat-square)](https://pypi.org/project/graphql-codegen)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen?style=flat-square)](https://github.com/tibdex/graphql-codegen/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](https://github.com/tibdex/graphql-codegen/blob/main/LICENSE)

This library turns a GraphQL schema and your operations into a typed Python client with:

- no imposed transport;
- no validation overhead;
- no runtime dependencies[^typing-extensions].

## Installation

```shell
pip install graphql-codegen
```

Or, with `uv`:

```shell
uv add --dev graphql-codegen
```

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
    # Dotted name from the module root, so written to `bookshop/client`.
    package: bookshop.client
```

Generate the client[^yaml]:

```shell
graphql-codegen bookshop/graphql.config.yml
```

The generated client:

- depends on nothing but the standard library[^typing-extensions];
- holds only the `enum` and `input` types your operations reach, so that it grows with your documents rather than with the schema.

Each GraphQL document also gets its own Python module holding its operations ([`get_order_graphql.py`](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/get_order_graphql.py) here).

Your type checker verifies every variable and every field of the response.
The code block below uses [`assert_never()`](https://docs.python.org/3/library/typing.html#typing.assert_never) and [`assert_type()`](https://docs.python.org/3/library/typing.html#typing.assert_type) so you can see what the type checker can prove.

<!-- file: bookshop/quickstart.py -->

```python
from typing import Literal, assert_never, assert_type

from bookshop.client.runtime import Client
from bookshop.client.schema import OrderStatus
from bookshop.get_order_graphql import GetOrder
from bookshop.transport import transport

client = Client(transport)
data = client(GetOrder({"id": "o1"}))
order = data["order"]

# `Query.order`'s type is nullable, so the type checker requires this test.
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

## Typing

### Type checking, not runtime validation

GraphQL is strongly typed, and the server:

- [validates](https://spec.graphql.org/September2025/#sec-Validation) each operation against its schema before running it;
- [responds](https://spec.graphql.org/September2025/#sec-Response) with exactly the operation's shape.

Client-side validation of responses thus mostly adds overhead[^breaking-changes].
What a Python client still lacks is knowing, while you write `order["status"]`, that the key exists and holds an `OrderStatus`.
That is a type checker's job, done once, before the code runs.

This library therefore generates exact types for your type checker and leaves each response as decoded from JSON.
Only [custom scalars](#custom-scalars) with a codec are converted, and only fields asserted [non-null](#non-null-fields) are checked.

> [!NOTE]
> Most other Python codegen libraries rely on [Pydantic](https://docs.pydantic.dev) models for both type checking and runtime validation.
> The [TypeScript GraphQL Code Generator](https://the-guild.dev/graphql/codegen), which sets the standard for generating code from GraphQL, does otherwise: it could validate each response with [Zod](https://zod.dev) (Pydantic's TypeScript counterpart) but relies on type checking alone.
> This library makes the same call.

### Operation types

Each operation gets a type for its variables and one for its data, both keyed by the names the GraphQL document uses.
Wherever GraphQL lets a value be one of several things, its Python type is a union:

- a selection on a [`union`](https://spec.graphql.org/September2025/#sec-Unions) or an [`interface`](https://spec.graphql.org/September2025/#sec-Interfaces) is one of several types, and becomes one type per concrete type, told apart by [`__typename`](https://spec.graphql.org/September2025/#sec-Type-Name-Introspection) (which the generator selects for you);
- an [`enum`](https://spec.graphql.org/September2025/#sec-Enums) is one of several values, and becomes a [`Literal`](https://docs.python.org/3/library/typing.html#typing.Literal);
- a [`@oneOf` `input`](https://spec.graphql.org/September2025/#sec-OneOf-Input-Objects) is one of several fields, and becomes a union of single-key types, so that a value with two keys fails type checking.

These unions are closed for the type checker, but nothing enforces them at runtime, since responses are [not validated](#type-checking-not-runtime-validation).

A server may add a `type` to a `union`, an implementation to an `interface`, a member to an `enum`, or a field to a [struct](#structs)'s `input` type without it being considered a breaking change.
A validating client would have raised an error before your code even ran, but this library lets the new value reach your code as sent, so you can choose what to do with it.

One way is to accept it in a `case _:` arm:

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
            # A member added after this client was generated.
            return status.replace("_", " ").capitalize()
```

The other way is to reject it with `case _ as never: assert_never(never)`, which:

- raises an `AssertionError` at runtime;
- makes the type checker point at every missing `case` once the client is regenerated.

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
Testing for a key narrows it to the `TypedDict`s with that key, and also covers any new `type` that `implements Printed`.

`length()` then narrows it to one `TypedDict` with a `match` on `__typename`, rejecting any other with `assert_never()`:

<!-- excerpt: bookshop/app.py -->

```python
def length(publication_id: str, /, *, client: Client) -> str:
    data = client(GetPublication({"id": publication_id}))
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

The names the generator adds, such as `_builtins` or `_GetBookData_book`, avoid every other name in their module, so none can shadow another.

## Client

### Sans-IO

The small [sans-IO](https://sans-io.readthedocs.io) runtime is copied into the generated package, and its [public API](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/client/runtime/__init__.py) is limited to:

<!-- excerpt: bookshop/client/runtime/__init__.py -->

```python
from .client import (
    AsyncClient as AsyncClient,
    AsyncSubscriptionClient as AsyncSubscriptionClient,
    Client as Client,
    SubscriptionClient as SubscriptionClient,
)
from .error import (
    ClientError as ClientError,
    Error as Error,
    ExecutionError as ExecutionError,
    Location as Location,
    ProtocolError as ProtocolError,
    RequestError as RequestError,
    ResponseError as ResponseError,
    UnexpectedNullError as UnexpectedNullError,
)
from .injection import OMITTED as OMITTED
from .operation import Operation as Operation, Request as Request
```

A transport is a function from a request body to a response body, so any HTTP client (synchronous or asynchronous) works, and so does anything else that carries `bytes`.
`Client`, `AsyncClient`, `SubscriptionClient`, and `AsyncSubscriptionClient` take the same generated operations, so one generation serves both synchronous and asynchronous code.
Each client forwards every argument but the first (the request) to its transport, type checked against the transport's signature.

As an example, the bookshop's asynchronous transport uses [httpx2](https://pydantic.dev/docs/httpx2) and accepts a `timeout` (and nothing else):

<!-- excerpt: bookshop/async_transport.py -->

```python
http = httpx2.AsyncClient(base_url="https://bookshop.example")
HEADERS = {"Accept": mime_type.GRAPHQL_RESPONSE, "Content-Type": mime_type.JSON}


async def transport(body: bytes, /, *, timeout: float | None = None) -> bytes:
    response = await http.post(
        "/graphql", content=body, headers=HEADERS, timeout=timeout
    )

    # GraphQL over HTTP sends a request error as a response with a 4xx status.
    if not response.headers.get("Content-Type", "").startswith(
        mime_type.GRAPHQL_RESPONSE
    ):
        response.raise_for_status()

    return response.content
```

A call through a client over this transport can thus pass a `timeout`:

<!-- file: bookshop/async_app.py -->

```python
from bookshop.app_graphql import GetBook
from bookshop.async_transport import AsyncClient
from bookshop.scalar import ISBN


async def title(isbn: ISBN, /, *, client: AsyncClient) -> str:
    data = await client(GetBook({"lookup": {"isbn": isbn}}), timeout=5.0)
    return data["book"]["title"]
```

[`async_transport.py`](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/async_transport.py) also streams a subscription's Server-Sent Events, and [`transport.py`](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/transport.py) does both with the standard library alone.

### Subscriptions

A subscription client works over any transport yielding one body per event, [Server-Sent Events](https://github.com/graphql/graphql-over-http/blob/main/rfcs/GraphQLOverSSE.md), [`graphql-transport-ws`](https://github.com/graphql/graphql-over-http/blob/main/rfcs/GraphQLOverWebSocket.md), or [multipart HTTP](https://github.com/graphql/graphql-over-http/blob/main/rfcs/IncrementalDelivery.md) alike:

<!-- excerpt: bookshop/app.py -->

```python
def watch(order_id: str, /, *, client: SubscriptionClient) -> list[OrderStatus]:
    """Follow the order until it is delivered, and return its statuses."""
    statuses: list[OrderStatus] = []
    events = client(OnOrderStatusChanged({"orderId": order_id}))

    # Closing the stream, however the loop ends, unsubscribes.
    with closing(events):
        for event in events:
            statuses.append(event["orderStatusChanged"]["status"])

            if statuses[-1] == "DELIVERED":
                break

    return statuses
```

### Merging

Sometimes you only know at runtime which operations to send together, or you want to group the same queries in many combinations, but writing each as its own operation in a `.graphql` file is impractical.
Combining several operations into one request can also let the server answer faster, seeing the whole picture instead of independent requests asking for overlapping data.
Some other codegen libraries let you build operations at runtime for this, giving up type safety.
This library instead merges operations written ahead of time into one request, so that each result keeps its exact type.

A tuple of queries, or of mutations, runs in one call to the transport, each result typed by its own operation:

<!-- excerpt: bookshop/app.py -->

```python
def book_and_similar(
    isbn: ISBN, text: str, /, *, client: Client
) -> tuple[str, list[str]]:
    # Two queries in one call to the transport.
    book_data, search_data = client(
        (
            GetBook({"lookup": {"isbn": isbn}}),
            Search({"text": text}),
        )
    )
    assert_type(book_data, GetBookData)
    assert_type(search_data, SearchData)
```

A list built at runtime also runs in one call to the transport, whatever its length.
Its results then share one type, the union of its operations' data types:

<!-- excerpt: bookshop/app.py -->

```python
def look_up(
    isbns: Sequence[ISBN], publication_ids: Sequence[str], /, *, client: Client
) -> tuple[GetBookData | GetPublicationData, ...]:
    # Any number of queries in one call to the transport.
    return client(  # ty: ignore[unsound-return-statement]  # Pyright and Pyrefly already infer this.
        [
            *(GetBook({"lookup": {"isbn": isbn}}) for isbn in isbns),
            *(GetPublication({"id": id_}) for id_ in publication_ids),
        ]
    )
```

### Errors

A response with errors raises a `RequestError` when the request failed before execution, and an `ExecutionError` when it carries partial data:

<!-- excerpt: bookshop/client/runtime/error.py -->

```python
class ExecutionError(ResponseError, Generic[_Data_co]):
    """The server raised errors executing the request, but sent the rest of the data.

    A field that raised is `null`, as is its nearest nullable parent if it is non-null.
    """

    data: Final[Mapping[str, object] | None]

    def parse_data(self) -> _Data_co | None:
        """Return the data converted as in a response without errors, in a fresh copy.
```

When several operations are merged, an `ExceptionGroup` holds one for each operation that fails.

You can also have the client return the error instead of raising it, by calling `returning_error()` on the request:

- the result is then typed as either the data or an `ExecutionError`, so you can tell them apart with `isinstance()`;
- in a [merge](#merging), you choose for each request whether its error is returned or raised;
- a subscription carries on past an event with errors.

<!-- excerpt: bookshop/app.py -->

```python
def cancel(order_ids: list[str], /, *, client: Client) -> list[str]:
    """Cancel the orders in one call to the transport, and explain each failure."""
    results = client(
        [
            CancelOrder({"input": {"order": order_id}}).returning_error()
            for order_id in order_ids
        ]
    )
    # Each error has a note naming its operation and the variables sent.
    return [
        f"{error.__notes__[0]} {error!s}"
        for error in results
        if isinstance(error, ExecutionError)
    ]


def track(order_id: str, /, *, client: Client) -> str:
    result = client(GetOrder({"id": order_id}).returning_error())

    if isinstance(result, ExecutionError):
        data = result.parse_data()
        assert_type(data, GetOrderData | None)
        return f"Partially loaded: {data} ({result!s})."
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

- a path starting with `..` is relative to the directory holding the package;
- a bare name, such as `str`, is a builtin;
- an unconfigured custom scalar is typed `object`.

A dotted name must name an attribute of a module, so a method such as `datetime.fromisoformat` needs a function of its own:

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

The client then converts each scalar's values on the way in and out, so your code only ever handles their Python types:

<!-- excerpt: bookshop/app.py -->

```python
def cheaper_than(limit: Decimal, /, *, client: Client) -> list[str]:
    data = client(ListBooks({"filter": {"priceBelow": limit}}))
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

Asserted on `book`, the field's type is then not optional:

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
def describe(isbn: ISBN, /, *, client: Client) -> str:
    variables: GetBookVariables = {"lookup": {"isbn": isbn}}

    try:
        data = client(GetBook(variables))
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
def run_saved_search(name: str, /, *, client: Client) -> list[str]:
    data = client(GetSavedSearch({"name": name}))
    search = data["savedSearch"]

    if search is None:
        return []

    book_filter = search["value"]
    assert_type(book_filter, BookFilter | None)
    # Sent back as is.
    books = client(ListBooks({"filter": book_filter}))
    return [book["title"] for book in books["books"]]
```

### Injectors

Some input values are `client`'s business rather than each `client()` call's.

For instance, an idempotency key makes retrying a mutation safe:

1. the connection drops after the server placed an order;
2. the transport sends the order again;
3. the key, unique to the order, tells the server it already placed it, so it does not charge the customer twice.

GraphQL has no built-in idempotency, so implementing it usually means adding the key as an argument or an input field of each mutation that needs it.
When many different mutation operations require idempotency, it becomes the concern of all their `client()` calls, each having to get hold of a key.
This applies to other concepts too, such as database transaction IDs.

This library handles such a value in one place instead, with an injector given when `client` is constructed.
The client then passes it to every variable or input field with the name given in the config.
No variables' type accepts it, so that no `client()` call can pass one by mistake:

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
  idempotencyKey: UUID
```

The generated package's [`injection.py`](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/client/injection.py) module types the injectors you must supply:

<!-- excerpt: bookshop/client/injection.py -->

```python
class InjectorFunctions(_compat.TypedDict, closed=True):
    """The functions supplying each injected value, by name.

    Where the value may be null, one returning `OMITTED` leaves it out, and one returning `None` sends `null`."""
    idempotencyKey: _typing.NotRequired[_abc.Callable[[], _scalar.UUID | None | _OMITTED]]

def injectors(functions: InjectorFunctions, /) -> _injection._Injectors:
    """Return what a client calls to supply the injected values, each serialized as its type says."""
    return _injection._Injectors(functions, injector_functions_type=InjectorFunctions)
```

You give the client its injectors once when building it:

<!-- excerpt: bookshop/__main__.py -->

```python
client = Client(
    transport,
    injectors=injectors({"idempotencyKey": uuid4}),
)
```

And no call can pass a key:

<!-- excerpt: bookshop/app.py -->

```python
def order(book_id: str, address: Address, /, *, client: Client) -> str:
    data = client(
        PlaceOrder(
            {"input": {"lines": [{"book": book_id}], "shippingAddress": address}}
        )
    )
```

The client injects new values on each call, so a retry belongs in your transport, which sends the same body, key included, again:

<!-- excerpt: bookshop/transport.py -->

```python
def transport(body: bytes, /, *, timeout: float | None = None) -> bytes:
    retries = 2

    while True:
        try:
            response = _post(
                "/graphql", body, accept=mime_type.GRAPHQL_RESPONSE, timeout=timeout
            )
        except HTTPError as error:
            # GraphQL over HTTP sends a request error as a response with a 4xx status.
            if error.headers.get_content_type() != mime_type.GRAPHQL_RESPONSE:
                raise

            response = error
        except ConnectionError:
            # The response was lost.
            if not retries:
                raise

            retries -= 1
            continue

        with response:
            return response.read()
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
from bookshop.client.schema import OrderStatus
from bookshop.get_order_graphql import GetOrder
```

> [!NOTE]
> When the config does not set `documentSiblingModule`, each document's operations and fragments go into a module of the generated package's `document` subpackage instead, such as `document/get_order.py` for `get_order.graphql`.
> The subpackage re-exports them all, [lazily](https://docs.python.org/3.15/reference/simple_stmts.html#compatibility-via-lazy-modules) from Python 3.15, so that a module is only imported when one of its operations or fragments is used.

## Python API

[`generate()`](https://github.com/tibdex/graphql-codegen/blob/main/src/graphql_codegen/generate.py) does the same as the command:

<!-- excerpt: src/graphql_codegen/generate.py -->

```python
class _Params(TypedDict, closed=True):
    document: DocumentNode
    schema: GraphQLSchema
    config: Config


def generate(**args: Unpack[_Params]) -> dict[PurePosixPath, bytes]:
    """Pure function returning the content of each file of the generated package by its path."""
```

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

[^typing-extensions]: Before Python 3.15, the client also needs [`typing_extensions`](https://typing-extensions.readthedocs.io), for features the standard library's [`typing`](https://docs.python.org/3/library/typing.html) does not have yet, so add it to your project's own dependencies.

[^bootstrapping]: This library is partly [bootstrapped](https://en.wikipedia.org/wiki/Bootstrapping_(compilers)): to fetch a schema from a URL, [it uses a client](https://github.com/tibdex/graphql-codegen/blob/main/src/graphql_codegen/_cli/_introspection.py) it generated itself from [`_introspection.graphql`](https://github.com/tibdex/graphql-codegen/blob/main/src/graphql_codegen/_cli/_introspection.graphql).

[^yaml]: A YAML config, like the quick start's, needs the `yaml` extra: install `graphql-codegen[yaml]` instead.
JSON and TOML configs need nothing more.

[^breaking-changes]: Well-behaved GraphQL APIs [avoid breaking changes](https://graphql.org/learn/schema-design/#versioning), adding fields and [deprecating](https://spec.graphql.org/September2025/#sec--deprecated) old ones instead.
Most breaking changes made after generation never reach the code anyway: removing or renaming a field, changing its arguments, or turning its type from an object into a leaf or the reverse invalidates the operation, so the server rejects it, and the `client()` call raises a [`RequestError`](#errors).
The few left would fail client-side validation as soon as the response arrives, but otherwise propagate deeper in your code, possibly unnoticed: a field becoming nullable, its leaf type changing, or it switching between a list and a single value.
The more often you regenerate against the deployed schema, the sooner your type checker catches such a change.

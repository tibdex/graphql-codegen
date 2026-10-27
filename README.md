# graphql-codegen

[![PyPI](https://img.shields.io/pypi/v/graphql-codegen?style=flat-square)](https://pypi.org/project/graphql-codegen)
[![Python](https://img.shields.io/pypi/pyversions/graphql-codegen?style=flat-square)](https://pypi.org/project/graphql-codegen)
[![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](https://github.com/tibdex/graphql-codegen/blob/main/LICENSE)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen?style=flat-square)](https://github.com/tibdex/graphql-codegen/blob/main/pyproject.toml)

This library compiles GraphQL documents into a typed Python package with:

- **no imposed transport**: bytes in, bytes out;
- **no runtime dependencies**[^typing-extensions]: nothing to install, nothing to upgrade;
- **no redundant validation**: just dicts, statically checked[^type-checkers].

Built-in:
- `query`, `mutation`, and [`subscription`](#subscriptions) with their variables and selected fields typed;
- [`union`, `interface`, `enum`, and `@oneOf` `input`](#operation-types) typed as Python unions;
- [`fragment`s](#fragments) typed on their own;
- [custom `scalar`s](#custom-scalars) converted to and from Python types;
- [partial responses](#errors) raised as errors, or returned with their data typed.

Opt-in:
- [merging](#merging) operations into a single transport request;
- [non-null assertions](#non-null-fields) on fields the schema declares nullable;
- [structs](#structs) typing a JSON `scalar` as an `input`;
- [injectors](#injectors) supplying a value from your transport rather than at every call;
- [colocation](#colocation) of each Python module with its GraphQL document.

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

Write your operations in GraphQL document files:

<!-- file: bookshop/get_order.graphql -->

```graphql
query GetOrder($id: ID!) {
  order(id: $id) {
    id
    status
  }
}
```

The generator reads [graphql-config](https://the-guild.dev/graphql/config), the same file GraphQL editor extensions and linters already use[^bootstrapping]:

<!-- excerpt: bookshop/graphql.config.yml -->

```yaml
# An SDL file here, but globs, introspection results, and server URLs work too.
schema: schema.graphqls
documents: "*.graphql"
# graphql-codegen's config, under its extension name.
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
- holds only the `enum` and `input` types your operations reach, so it grows with your documents rather than with the schema;
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
- [responds](https://spec.graphql.org/September2025/#sec-Response) with the selected fields and nothing else.

Client-side validation is redundant[^breaking-changes].
On the other hand, you still want to know that `order["status"]` exists and holds an `OrderStatus`, and to learn when an edited operation breaks that.
A type checker is the best way to get that safety.

The generated package therefore types each operation statically, and leaves each response as decoded from JSON (only [custom scalars](#custom-scalars) with a codec are converted).

> [!NOTE]
> Most other Python codegen libraries rely on [Pydantic](https://docs.pydantic.dev) models for both type checking and runtime validation.
> The [TypeScript GraphQL Code Generator](https://the-guild.dev/graphql/codegen), the reference for generating code from GraphQL, could validate each response too, with a library such as [Zod](https://zod.dev), but relies on type checking alone.
> This library makes the same call.

### Operation types

Each operation gets a type for its variables and one for its data, both keyed by the names the GraphQL document uses.
What may be absent is a [`NotRequired`](https://docs.python.org/3/library/typing.html#typing.NotRequired) key:

<!-- excerpt: bookshop/app.graphql -->

```graphql
query ListTitles($genres: [Genre!], $first: Int, $withGenres: Boolean! = false) {
  books(genres: $genres, first: $first) {
    ...TitleCard
  }
}

fragment TitleCard on Book {
  title
  genre @include(if: $withGenres)
}
```

<!-- excerpt: bookshop/app.py -->

```python
def titles(
    *genres: Genre, first: int | None = None, with_genres: bool | None = None
) -> list[str]:
    # `$first` is nullable and `$withGenres` has a default, so both keys are `NotRequired`.
    variables: ListTitlesVariables = {
        # A GraphQL list variable takes any `Sequence`, such as this `tuple`.
        "genres": genres,
    }

    # A key left out sends nothing, while `first: None` would send `null`.
    if first is not None:
        variables["first"] = first

    if with_genres is not None:
        variables["withGenres"] = with_genres

    data = send(ListTitles(variables))
    # A GraphQL list field in data is a Python `list`, as JSON decodes it.
    assert_type(data["books"], list[TitleCard])
    labels: list[str] = []

    for book in data["books"]:
        # Selected under `@include`, `genre` is a `NotRequired` key too.
        if "genre" in book:
            assert_type(book["genre"], Genre)
            labels.append(f"{book['title']} ({book['genre'].lower()})")
        else:
            labels.append(book["title"])

    return labels
```

Wherever GraphQL lets a value be one of several things, it becomes a Python union `type`:

- a selection on a [`union`](https://spec.graphql.org/September2025/#sec-Unions) or an [`interface`](https://spec.graphql.org/September2025/#sec-Interfaces) is one of several types, and becomes one type per concrete type, told apart by [`__typename`](https://spec.graphql.org/September2025/#sec-Type-Name-Introspection) (which the generator selects for you);
- an [`enum`](https://spec.graphql.org/September2025/#sec-Enums) is one of several values, and becomes a [`Literal`](https://docs.python.org/3/library/typing.html#typing.Literal);
- a [`@oneOf` `input`](https://spec.graphql.org/September2025/#sec-OneOf-Input-Objects) is one of several fields, and becomes a union of single-key types, so a value with two keys fails type checking.

These unions are closed for the type checker, but nothing enforces them at runtime, since responses are [not validated](#type-checking-over-runtime-validation).

A server may add a `type` to a `union`, an implementation to an `interface`, or a member to an `enum` at any time, since none of these is a breaking change.
A validating client would reject the new value before your code ever sees it.
Here, your code receives it as sent and decides what to do with it.

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

You can do the same on an `interface`, such as the bookshop's `Publication`:

<!-- excerpt: bookshop/schema.graphqls -->

```graphql
"Something the bookshop sells."
interface Publication {
  id: ID!
  title: String!
  price: Money!
}

interface Printed implements Publication {
  # …
  pageCount: Int!
}

type Book implements Publication & Printed {
  # …
}

type Magazine implements Publication & Printed {
  # …
}

type Audiobook implements Publication {
  # …
  "Its length, in minutes."
  duration: Int!
}
```

[`app.graphql`](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/app.graphql) selects a publication's length, in pages when it is printed and in minutes when it is an audiobook:

<!-- excerpt: bookshop/app.graphql -->

```graphql
query GetPublication($id: ID!) {
  publication(id: $id) {
    title
    ... on Printed {
      pages: pageCount
      ... on Book {
        isbn
      }
    }
    ... on Audiobook {
      duration
    }
  }
}
```

`GetPublicationData.publication` is then a union of one [`TypedDict`](https://docs.python.org/3/library/typing.html#typing.TypedDict) per concrete type: `Book`, `Magazine`, and `Audiobook`.
Checking for the `pages` key narrows it to the `TypedDict`s with that key, and at runtime admits any implementation of `Printed` added after codegen.
A `match` on `__typename` narrows it to one, and `assert_never()` rejects any other:

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

### Fragments

A fragment becomes a `TypedDict` of its own, extended by the `TypedDict` of each selection spreading it:

<!-- excerpt: bookshop/app.graphql -->

```graphql
fragment BookCard on Book {
  id
  title
  price
  author {
    name
  }
}

query ListBooks($filter: BookFilter, $first: Int) {
  books(filter: $filter, first: $first) {
    ...BookCard
    genre
  }
}
```

<!-- excerpt: bookshop/app.py -->

```python
def card(book: BookCard, /) -> str:
    author = book["author"]
    by = "an anthology" if author is None else f"by {author['name']}"
    return f"{book['title']}, {by}"


def catalog(first: int, /) -> list[str]:
    data = send(ListBooks({"first": first}))
    return [card(book) for book in data["books"]]
```

A fragment on an `interface` or a `union` becomes a union of one `TypedDict` per concrete type it covers, told apart by `__typename`:

<!-- excerpt: bookshop/app.graphql -->

```graphql
query GetPages($id: ID!) {
  publication(id: $id) {
    ...PrintedLength
  }
}

fragment PrintedLength on Printed {
  title
  pages: pageCount
  ... on Book {
    isbn
  }
}
```

<!-- excerpt: bookshop/app.py -->

```python
def printed_length(printed: PrintedLength, /) -> str:
    assert_type(printed["__typename"], Literal["Book", "Magazine"])
    return f"{printed['title']} has {printed['pages']} pages."


def pages(publication_id: str, /) -> str:
    data = send(GetPages({"id": publication_id}))
    publication = data["publication"]

    if publication is None or "pages" not in publication:
        return "No pages."

    return printed_length(publication)
```

### No name clashes

Nothing prevents a schema or a document from using names that clash with Python keywords (`class`, `from`), standard library names the generated code uses (`list`, `Sequence`), or the generator's own helpers.

Since `TypedDict` keys are strings rather than identifiers, a GraphQL field named `from` keeps the key `"from"` in Python.
An `input` named `list` keeps its name too, since the generated code refers to builtins as `_builtins.list`.
Only the names the generator adds, such as that `_builtins` import alias or `_GetBookData_book` for the `book` selection of `GetBookData`, are chosen to avoid every other name in their module, so none can shadow another.

## Runtime

### Sans-IO

Shipping a transport is a slippery slope: an HTTP client to pick, then headers, authentication, timeouts, and retries.
Every project has its own opinion on each of these, and the GraphQL specification itself [leaves the transport to each service](https://spec.graphql.org/September2025/#note-81789).
*The only winning move is not to play.*

The small [sans-IO](https://sans-io.readthedocs.io) runtime copied into the generated package therefore has a [public API](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/graphql/runtime/__init__.py) limited to:

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
from .operation import (
    Operation as Operation,
    OperationRequest as OperationRequest,
)
from .request import (
    PreparedRequest as PreparedRequest,
    Request as Request,
)
```

In particular:

1. `Operation[Type, Variables, Data].__call__()` takes the `Variables` and returns a `Request[Type, Data]`;
2. `Request[Type, Data].prepare()` returns a `PreparedRequest[Data]`;
3. `PreparedRequest.__bytes__()` returns the request body to send;
4. `PreparedRequest[Data].parse()` returns the response body as `Data`.

You compose these methods with your own transport, built on any HTTP client (synchronous, asynchronous, or callback-based) or anything else that carries `bytes`.

The transport takes only a few lines (and stays that short as you add operations).
For example, the bookshop's asynchronous transport uses [httpx2](https://pydantic.dev/docs/httpx2):

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
    prepared = request.prepare()
    response = await http.post("/graphql", content=bytes(prepared), timeout=timeout)

    # GraphQL over HTTP sends a request error as a response with a 4xx or 5xx status.
    if not response.headers.get("Content-Type", "").startswith(GRAPHQL_RESPONSE):
        response.raise_for_status()

    return prepared.parse(response.content)
```

`Data` then reaches your transport's caller, so each call is typed by its operation:

<!-- excerpt: bookshop/async_app.py -->

```python
async def title(isbn: ISBN, /) -> str:
    data = await send(GetBook({"lookup": {"isbn": isbn}}), timeout=5.0)
    assert_type(data, GetBookData)
    return data["book"]["title"]
```

[`transport.py`](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/transport.py) does the same with the standard library alone.
If your transport treats queries and mutations differently, such as sending queries with [`GET`](https://http-spec.graphql.org/draft/#sec-GET), you can switch on `Request.operation_type`.

### Subscriptions

A subscription's response is a stream of bodies, one per event, and `PreparedRequest.parse()` reads each as it comes.
The bookshop's `subscribe()` reads [Server-Sent Events](https://github.com/graphql/graphql-over-http/blob/main/rfcs/GraphQLOverSSE.md); [`graphql-transport-ws`](https://github.com/graphql/graphql-over-http/blob/main/rfcs/GraphQLOverWebSocket.md) or [multipart HTTP](https://github.com/graphql/graphql-over-http/blob/main/rfcs/IncrementalDelivery.md) would be read the same way:

<!-- excerpt: bookshop/async_transport.py -->

```python
async def subscribe[Data](
    request: Request[Literal["subscription"], Data], /
) -> AsyncGenerator[Data, None]:
    prepared = request.prepare()

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

Sometimes you only know at runtime which operations go together, and writing every combination as its own operation in a document file is impractical.
Some other codegen libraries let you build operations at runtime for this, giving up type safety.
Here, you can use `merge()` to combine several queries, or several mutations, written ahead of time into one request, while each result keeps its exact type:

<!-- excerpt: bookshop/app.py -->

```python
def book_and_similar(isbn: ISBN, text: str, /) -> tuple[str, list[str]]:
    # Two queries in a single transport request.
    book_data, search_data = send(
        merge(
            GetBook({"lookup": {"isbn": isbn}}),
            Search({"text": text}),
        )
    )
    assert_type(book_data, GetBookData)
    assert_type(search_data, SearchData)
```

A list built at runtime can be merged too, and its results are typed as the union of its operations' data types:

<!-- excerpt: bookshop/app.py -->

```python
def look_up(
    isbns: Sequence[ISBN], publication_ids: Sequence[str], /
) -> tuple[GetBookData | GetPublicationData, ...]:
    # Any number of queries in a single transport request.
    queries: list[
        OperationRequest[Literal["query"], GetBookData | GetPublicationData]
    ] = [
        *(GetBook({"lookup": {"isbn": isbn}}) for isbn in isbns),
        *(GetPublication({"id": id_}) for id_ in publication_ids),
    ]
    return send(merge(queries))
```

`merge()` joins the requests into one operation (telling their root fields and variables apart with an index) and splits the response back the same way.
The server [runs mutations in order and can resolve queries in parallel](https://spec.graphql.org/September2025/#sec-Normal-and-Serial-Execution).
As a bonus, most servers batch and cache per request, so one big query can execute faster than several small concurrent ones.

### Errors

On a response with errors, `PreparedRequest.parse()` raises:

- a `RequestError` in place of data, when the request failed before execution;
- an `ExecutionError` beside partial data, when some fields failed.

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

In an `except ExecutionError as error:` block, `error.parse_data()` returns a `Mapping[str, object] | None`, since Python types what a call returns, but not what it raises.
You can instead call `OperationRequest.returning_error()` to get a request for which `PreparedRequest.parse()` returns its `ExecutionError` rather than raising it, so the partial data keeps the operation's type:

- the result is then typed `Data | ExecutionError[Data]`, so you can tell them apart with `isinstance()`;
- when [merging](#merging), you choose for each request whether its error is returned or raised;
- in a subscription, an event with errors is yielded like any other, so your transport has nothing to catch.

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

[Custom scalars](https://spec.graphql.org/September2025/#sec-Scalars.Custom-Scalars) travel as JSON values in a format decided by the server (such as an ISO 8601 string for a date).
You can give each one a Python type, with a codec converting its values when they differ from their JSON form:

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

A scalar without a codec must be typed as what JSON decodes to: a builtin such as `str`, or a `NewType` or alias of one, like the bookshop's `ISBN = NewType("ISBN", str)`.

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
> For example, with Pydantic, build `adapter = TypeAdapter(YourType)` once, then use `decode = adapter.validate_python` and `encode = partial(adapter.dump_python, mode="json")`.

`Request.prepare()` then encodes each scalar's values, and `PreparedRequest.parse()` decodes them, so your code only ever handles their Python types:

<!-- excerpt: bookshop/app.py -->

```python
def cheaper_than(limit: Decimal, /) -> list[str]:
    data = send(ListBooks({"filter": {"priceBelow": limit}}))
    labels: list[str] = []

    for book in data["books"]:
        price = book["price"]
        assert_type(price, Decimal)
        labels.append(f"{card(book)}: {price:.2f}")

    return labels
```

### Non-null fields

Schemas often make fields nullable, such as a lookup that may find nothing.
However, you may have more context than the server and expect a specific lookup to always succeed.
Or you may want your code to fail fast on a `null`, without writing `if value is None: raise …` at every use.
Either way, you can declare that through a client directive, adapted from the [Client Controlled Nullability](https://github.com/graphql/graphql-wg/blob/main/rfcs/ClientControlledNullability.md) RFC.
The generator strips the directive from the document, so the server never sees it:

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
    data = send(GetBook({"lookup": {"isbn": isbn}}))
    book = data["book"]
    # `@nonNull` took `| None` out of `book`'s type, so no check is needed before indexing it.
    author = book["author"]
    # `@nonNull` covers `book` alone, so `author` keeps its `| None`.
    by = "an anthology" if author is None else f"by {author['name']}"
```

If `book` still comes back `null`, `PreparedRequest.parse()` raises an `UnexpectedNullError`:

<!-- excerpt: bookshop/graphql/runtime/error.py -->

```python
class UnexpectedNullError(Error):
    """A field marked `@nonNull` came back `null`."""

    @property
    def path(self) -> Sequence[str | int]:
        """The keys and list indices leading from the root of the data to the `null`, as in a GraphQL error's `path`."""
```

> [!NOTE]
> On an [`ExecutionError`](#errors), `data` keeps the partial data as the server sent it.
> `parse_data()` does not raise on an `@nonNull` field found `null`: it moves the `null` up to the nearest nullable parent instead, as the server does for a field the schema declares non-null.

### Structs

A selection set has a fixed depth, so data of unbounded depth, such as a tree, can only come back as an untyped custom scalar.
The [Struct RFC](https://github.com/graphql/graphql-wg/blob/main/rfcs/Struct.md) proposes a new `struct` keyword: selected without a selection set, a field of a struct type returns its value whole, like a scalar.
Until the RFC is accepted and widely adopted, you can get the same result by following this library's convention in your schema:

> - a designated `interface` has a single field (of any name) typed with any custom scalar;
> - each `type` that `implements` this interface is named after an `input` followed by the interface's name;
> - the field's payload in each such `type` has the shape of that `input` (even a recursive one).

The generated package then types the payload with that `input`, and never mentions the custom scalar.

The bookshop names that interface `Struct`:

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

So the same `BookFilter` goes from one to the other as is, typed all the way:

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

Some values are better supplied in one place than at every call.

The customer an app acts for is one example: every order carries the customer's identifier, but the functions placing orders should not have to pass that identifier around.

Idempotency is another example.
An idempotency key makes retrying a mutation safe:

1. the connection drops after the server placed an order;
2. the transport sends the order again;
3. the key, unique to the order, tells the server it already placed it, so it does not charge the customer twice.

However, GraphQL has no built-in idempotency, so the key ends up as an argument or an input field of each mutation needing one, and getting hold of a key becomes every caller's concern.

You can instead supply the customer ID or the idempotency key in one place, with an injector given to `Request.prepare()`.
The injector's value goes to every variable or input field with the name given in the config:

<!-- excerpt: bookshop/graphql.config.yml -->

```yaml
extensions:
  pythonCodegen:
    # …
    injectorNames: [customer, idempotencyKey]
```

The customer is an argument of `Query.orders` and a field of `PlaceOrderInput` and `CancelOrderInput`.
Both inputs also hold the idempotency key:

<!-- excerpt: bookshop/schema.graphqls -->

```graphql
type Query {
  # …
  "The orders of the customer to act for, the authenticated account itself when omitted."
  orders(customer: ID): [Order!]!
  # …
}

input PlaceOrderInput {
  # …
  customer: ID
  "Makes placing the same order twice harmless: the client sends a new one per order."
  idempotencyKey: UUID!
  # …
}

input CancelOrderInput {
  # …
  customer: ID
  # …
  idempotencyKey: UUID!
  order: ID!
}
```

`ListOrders` declares `$customer` for the injector to fill:

<!-- excerpt: bookshop/app.graphql -->

```graphql
query ListOrders($customer: ID) {
  orders(customer: $customer) {
    id
    status
  }
}
```

An injected name must have the same type (nullability aside) wherever it appears.
The generated package's [`injection.py`](https://github.com/tibdex/graphql-codegen/blob/main/bookshop/graphql/injection.py) module types the function you supply for each name: that function must return the value as its scalar's Python type, or `OMITTED` to leave a nullable one out.
`injectors.py` builds them once:

<!-- file: bookshop/injectors.py -->

```python
from contextvars import ContextVar
from typing import Final
from uuid import uuid4

from bookshop.graphql.injection import injectors
from bookshop.graphql.runtime import OMITTED

CURRENT_CUSTOMER: Final = ContextVar[str | None]("CURRENT_CUSTOMER", default=None)


def customer() -> str | OMITTED:
    current = CURRENT_CUSTOMER.get()
    if current is None:
        # The app authenticates with its own account and acts for the customer it serves.
        # With nobody to serve, the field is left out rather than null, so the server acts for that account.
        return OMITTED
    return current


INJECTORS: Final = injectors(
    {
        "customer": customer,
        "idempotencyKey": uuid4,
    }
)
```

No variables' type accepts the customer ID or the idempotency key, so no caller can pass one by mistake:

<!-- excerpt: bookshop/app.py -->

```python
def order(book_id: str, address: Address, /) -> str:
    data = send(
        PlaceOrder(
            {"input": {"lines": [{"book": book_id}], "shippingAddress": address}}
        )
    )


def order_ids() -> list[str]:
    data = send(ListOrders({}))
    return [order["id"] for order in data["orders"]]
```

`Request.prepare()` calls the injectors once, so the idempotency key is in `bytes(prepared)`.
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
        # Declared, since mypy types a variable from its first assignment alone.
        response: HTTPResponse | HTTPError

        try:
            response = _post(
                "/graphql",
                request_body,
                # Falls back to the plain JSON media type for a server predating the GraphQL one.
                accept="application/graphql-response+json, application/json;q=0.9",
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
A feature's generated operations then live away from its documents and the code calling them.
A project split into several packages cannot have each package own its operations either.
Since this library turns each document into its own Python module, you can opt into generating that module next to its document:

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

## Generator API

To run the generator programmatically, call [`generate()`](https://github.com/tibdex/graphql-codegen/blob/main/src/graphql_codegen/generate.py):

<!-- excerpt: src/graphql_codegen/generate.py -->

```python
class _Params(TypedDict, closed=True):
    document: DocumentNode
    schema: GraphQLSchema
    config: Config


def generate(**args: Unpack[_Params]) -> dict[PurePosixPath, bytes]:
    """Pure function returning the content of each file of the generated package by its path."""
```

`Config` and the types it is made of complete the [public API](https://github.com/tibdex/graphql-codegen/blob/main/src/graphql_codegen/__init__.py):

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

[^typing-extensions]: Before Python 3.15, the generated package needs [`typing-extensions`](https://github.com/python/typing_extensions) to access the latest [`typing`](https://docs.python.org/3/library/typing.html) features.
The generator declares that dependency, but the generated package is copied into your project rather than installed, so add `typing-extensions` to your own dependencies.

[^type-checkers]: The generated code is tested against [mypy](https://github.com/python/mypy), [Pyright](https://github.com/microsoft/pyright), [Pyrefly](https://github.com/facebook/pyrefly), and [ty](https://github.com/astral-sh/ty).

[^bootstrapping]: This library is partly [bootstrapped](https://en.wikipedia.org/wiki/Bootstrapping_(compilers)): to fetch a schema from a URL, [it uses an operation](https://github.com/tibdex/graphql-codegen/blob/main/src/graphql_codegen/_cli/_introspection.py) it generated itself from [`_introspection.graphql`](https://github.com/tibdex/graphql-codegen/blob/main/src/graphql_codegen/_cli/_introspection.graphql).

[^yaml]: A YAML config, like the quick start's, needs the `yaml` extra: install `graphql-codegen[yaml]` instead.
JSON and TOML configs need nothing more.

[^breaking-changes]: Well-behaved GraphQL APIs [avoid breaking changes](https://graphql.org/learn/schema-design/#versioning), adding fields and [deprecating](https://spec.graphql.org/September2025/#sec--deprecated) old ones instead.
Most breaking changes made after codegen never reach the code anyway: removing or renaming a field, changing its arguments, or turning its type from an object into a leaf or the reverse invalidates the operation, so the server rejects it, and `PreparedRequest.parse()` raises a [`RequestError`](#errors).
The few left are a field becoming nullable, its leaf type changing, or a field switching between a list and a single value.
Client-side validation would catch these as the response arrives.
Without it, they surface deeper in your code, or not at all.
The more often you rerun the codegen against the deployed schema, the sooner your type checker catches such a change.

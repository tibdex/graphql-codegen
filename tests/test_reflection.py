from __future__ import annotations

from datetime import UTC, datetime
from threading import Thread
from typing import Annotated, Literal, NotRequired, Required

import pytest

from graphql_codegen.runtime import UnexpectedNullError

# As generated types are: `typing.is_typeddict` once missed `typing_extensions.TypedDict`, silently making a transform a no-op.
from graphql_codegen.runtime._compat import ReadOnly, TypedDict
from graphql_codegen.runtime._reflection import (
    NON_NULL,
    Codec,
    _Builder,
    build_parser,
    build_serializer,
)

type DateTime = Annotated[
    datetime, Codec(decode=datetime.fromisoformat, encode=datetime.isoformat)
]

_OPENED = datetime(2026, 9, 26, tzinfo=UTC)
_OPENED_ON_THE_WIRE = "2026-09-26T00:00:00+00:00"


class _Card(TypedDict):
    title: str
    genres: list[str]


class _Review(TypedDict):
    postedAt: DateTime | None


class _Shelf(TypedDict):
    reviewPages: Annotated[list[list[_Review]], NON_NULL]
    label: Annotated[str, NON_NULL]


class _Data(TypedDict):
    shelves: list[_Shelf]
    featured: _Shelf | None


_BookTypename = TypedDict("_BookTypename", {"__typename": ReadOnly[Literal["Book"]]})
_PeriodicalTypename = TypedDict(
    "_PeriodicalTypename", {"__typename": ReadOnly[Literal["Magazine", "Newspaper"]]}
)


class _Book(_BookTypename):
    publishedAt: DateTime


class _Periodical(_PeriodicalTypename):
    publishedAt: str


type _Publication = _Book | _Periodical


class _Search(TypedDict):
    search: list[_Publication]


class _PublishedAfter(TypedDict, closed=True):
    publishedAfter: Required[DateTime]


class _Combination(TypedDict, closed=True):
    filters: Required[list[_Filter]]


class _Combined(TypedDict, closed=True):
    combined: Required[_Combination]


type _Filter = _PublishedAfter | _Combined


class _Variables(TypedDict, closed=True):
    filter: Required[_Filter]
    since: NotRequired[DateTime]


class _SavedSearch(TypedDict):
    """A struct's payload, typed by an input type, comes back in a response."""

    filter: _Filter


class _Author(TypedDict):
    latestBook: _LatestBook | None
    joinedAt: DateTime


class _LatestBook(TypedDict):
    author: _Author | None


def test_a_type_is_built_by_one_thread_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _Builder("parse")
    parsed: list[object] = []

    def parse() -> None:
        convert = builder.build(_LatestBook)
        assert convert is not None
        parsed.append(convert({"author": None}))

    other = Thread(target=parse)
    compute_build = _Builder._compute_build

    def compute_build_while_other_parses(self: _Builder, type_: object, /) -> object:
        if type_ is _LatestBook:
            other.start()
            # Long enough for the other thread to parse, were it not waiting for this build.
            other.join(timeout=0.1)

        return compute_build(self, type_)

    monkeypatch.setattr(_Builder, "_compute_build", compute_build_while_other_parses)
    builder.build(_LatestBook)
    other.join()

    assert parsed == [{"author": None}]


class _Hardcover(TypedDict):
    releasedAt: DateTime


class _Paperback(TypedDict):
    releasedAt: DateTime


class _Ambiguous(TypedDict):
    edition: _Hardcover | _Paperback


class _Mixed(TypedDict):
    releasedAt: DateTime | list[DateTime]


def test_a_type_needing_nothing_builds_to_none() -> None:
    assert build_parser(_Card, partial=False) is None
    assert build_serializer(_Card) is None
    # A `@nonNull` field is checked when parsing, but means nothing when serializing.
    assert build_parser(_Shelf, partial=False) is not None


_W = _OPENED_ON_THE_WIRE
_P = _OPENED


@pytest.mark.parametrize(
    ("type_", "data", "parsed"),
    [
        pytest.param(
            _Data,
            {
                "shelves": [
                    {
                        "reviewPages": [[{"postedAt": _W}, {"postedAt": None}], []],
                        "label": "Poetry",
                    }
                ],
                "featured": None,
            },
            {
                "shelves": [
                    {
                        "reviewPages": [[{"postedAt": _P}, {"postedAt": None}], []],
                        "label": "Poetry",
                    }
                ],
                "featured": None,
            },
            id="a scalar is parsed however deep",
        ),
        pytest.param(
            _Data,
            {"shelves": [], "featured": None},
            {"shelves": [], "featured": None},
            id="a field asserted non-null under a null parent is not checked",
        ),
        pytest.param(
            _Search,
            {
                "search": [
                    {"__typename": "Book", "publishedAt": _W},
                    {"__typename": "Newspaper", "publishedAt": "Monday"},
                ]
            },
            {
                "search": [
                    {"__typename": "Book", "publishedAt": _P},
                    {"__typename": "Newspaper", "publishedAt": "Monday"},
                ]
            },
            id="a union converts only the member its typename names",
        ),
        pytest.param(
            _Search,
            {"search": [{"__typename": "Audiobook", "publishedAt": _W}]},
            {"search": [{"__typename": "Audiobook", "publishedAt": _W}]},
            id="a member added after generation passes untouched",
        ),
        pytest.param(
            _SavedSearch,
            {"filter": {"publishedBefore": _W}},
            {"filter": {"publishedBefore": _W}},
            id="and so does a `@oneOf` member, which a struct's payload may hold",
        ),
        pytest.param(
            _LatestBook,
            {
                "author": {
                    "latestBook": {"author": {"latestBook": None, "joinedAt": _W}},
                    "joinedAt": _W,
                }
            },
            {
                "author": {
                    "latestBook": {"author": {"latestBook": None, "joinedAt": _P}},
                    "joinedAt": _P,
                }
            },
            id="a type needing work only through a cycle still gets it",
        ),
    ],
)
def test_parsing_converts_in_place(
    type_: type, data: dict[str, object], parsed: dict[str, object]
) -> None:
    parse = build_parser(type_, partial=False)
    assert parse is not None

    parse(data)

    assert data == parsed


@pytest.mark.parametrize(
    ("data", "path", "message"),
    [
        pytest.param(
            {
                "shelves": [
                    {"reviewPages": [], "label": "a"},
                    {"reviewPages": None, "label": "b"},
                ],
                "featured": None,
            },
            ["shelves", 1, "reviewPages"],
            "Expected `shelves[1].reviewPages` to not be null.",
            id="in a list",
        ),
        pytest.param(
            {"shelves": [], "featured": {"reviewPages": None, "label": "Poetry"}},
            ["featured", "reviewPages"],
            "Expected `featured.reviewPages` to not be null.",
            id="under a nullable parent, which only a response with errors nulls instead",
        ),
    ],
)
def test_an_unexpected_null_raises_naming_its_exact_path(
    data: dict[str, object], path: list[object], message: str
) -> None:
    parse = build_parser(_Data, partial=False)
    assert parse is not None

    with pytest.raises(UnexpectedNullError) as error_info:
        parse(data)

    assert (error_info.value.path, str(error_info.value)) == (path, message)


@pytest.mark.parametrize(
    ("variables", "serialized"),
    [
        pytest.param(
            {
                "filter": {
                    "combined": {
                        "filters": [
                            {"publishedAfter": _P},
                            {"combined": {"filters": []}},
                        ]
                    }
                }
            },
            {
                "filter": {
                    "combined": {
                        "filters": [
                            {"publishedAfter": _W},
                            {"combined": {"filters": []}},
                        ]
                    }
                }
            },
            id="a recursive oneOf input",
        ),
        pytest.param(
            {"filter": {"publishedAfter": _P}, "since": _P},
            {"filter": {"publishedAfter": _W}, "since": _W},
            id="an optional key present",
        ),
    ],
)
def test_serializing_copies_rather_than_mutating(
    variables: dict[str, object], serialized: dict[str, object]
) -> None:
    serialize = build_serializer(_Variables)
    assert serialize is not None
    before = repr(variables)

    assert serialize(variables) == serialized
    assert repr(variables) == before


@pytest.mark.parametrize(
    ("type_", "message"),
    [
        pytest.param(
            _Ambiguous,
            "Cannot tell the members",
            id="whose members cannot be told apart",
        ),
        pytest.param(
            _Mixed,
            "needing work to be TypedDicts",
            id="whose members needing work are not TypedDicts",
        ),
    ],
)
def test_a_union_is_rejected_when_built(type_: type, message: str) -> None:
    with pytest.raises(TypeError, match=message):
        build_parser(type_, partial=False)

from datetime import datetime
from typing import NewType

ISBN = NewType("ISBN", str)
"""A string on the wire and in Python that a type checker tells apart from others."""


def decode_datetime(value: str, /) -> datetime:
    return datetime.fromisoformat(value)


def encode_datetime(value: datetime, /) -> str:
    return value.isoformat()

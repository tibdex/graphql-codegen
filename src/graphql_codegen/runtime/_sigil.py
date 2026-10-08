# Copied into each generated package, where this line says not to edit it.

from typing import Final

SIGIL: Final = "§"
"""Where an operation's index in a merge goes in its document: after each root field's response name, as its alias, and after each variable's name.

An operation run alone drops it, leaving each root field aliased as itself, which GraphQL executes as if it were not aliased.
A document without it cannot be merged: a subscription's, or that of an operation with directives of its own.
The generator escapes it in strings, so that every one left is a sigil.
"""

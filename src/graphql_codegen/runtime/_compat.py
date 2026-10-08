# Copied into each generated package, where this line says not to edit it.

import sys

# Closed TypedDicts (PEP 728) and sentinels (PEP 661) are the last to reach the standard library.
if sys.version_info >= (3, 15):
    from typing import (
        ReadOnly as ReadOnly,
        TypedDict as TypedDict,
        TypeVar as TypeVar,
        get_type_hints as get_type_hints,
        is_typeddict as is_typeddict,
    )

    # The class of what the builtin `sentinel()` returns.
    SentinelType = sentinel  # noqa: F821
else:
    from typing_extensions import (
        ReadOnly as ReadOnly,
        Sentinel,
        TypedDict as TypedDict,
        TypeVar as TypeVar,
        get_type_hints as get_type_hints,
        is_typeddict as is_typeddict,
    )

    SentinelType = Sentinel

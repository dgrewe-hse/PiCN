# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""JCS (RFC 8785) canonical JSON for signed artefact bodies.

Only the JSON subset needed by agentic artefacts is supported: objects, arrays,
strings, integers, booleans, and null. **Floats are rejected** — signed bodies
must use strings or scaled integers so signature bytes stay unambiguous.
"""

from __future__ import annotations

from typing import Any


class FloatInSignedBodyError(ValueError):
    """Raised when a float appears anywhere in a value destined for JCS."""


def reject_floats(value: Any, path: str = "$") -> None:
    """Raise :class:`FloatInSignedBodyError` if ``value`` contains a float.

    :param value: Arbitrary JSON-like structure.
    :param path: Location path for error messages.
    :raises FloatInSignedBodyError: On any float occurrence.
    """
    # ``bool`` is a subclass of ``int``; check it before the float/int branches.
    if isinstance(value, bool) or value is None:
        return
    if type(value) is float:
        raise FloatInSignedBodyError(f"float not allowed in signed body at {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            reject_floats(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for i, child in enumerate(value):
            reject_floats(child, f"{path}[{i}]")
        return
    if isinstance(value, (str, int)):
        return
    raise TypeError(f"unsupported JCS type at {path}: {type(value)!r}")


def _escape_string(s: str) -> str:
    parts: list[str] = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            parts.append('\\"')
        elif ch == "\\":
            parts.append("\\\\")
        elif ch == "\b":
            parts.append("\\b")
        elif ch == "\f":
            parts.append("\\f")
        elif ch == "\n":
            parts.append("\\n")
        elif ch == "\r":
            parts.append("\\r")
        elif ch == "\t":
            parts.append("\\t")
        elif o < 0x20:
            parts.append(f"\\u{o:04x}")
        else:
            parts.append(ch)
    parts.append('"')
    return "".join(parts)


def _serialize(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is float:
        raise FloatInSignedBodyError("float not allowed in signed body")
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return _escape_string(value)
    if isinstance(value, list):
        return "[" + ",".join(_serialize(v) for v in value) + "]"
    if isinstance(value, dict):
        # RFC 8785: sort keys lexicographically by UTF-16 code units; for
        # BMP-only keys (ours) this matches Unicode code-point order.
        items = sorted(value.items(), key=lambda kv: kv[0])
        inner = ",".join(
            f"{_escape_string(key)}:{_serialize(child)}" for key, child in items
        )
        return "{" + inner + "}"
    raise TypeError(f"unsupported JCS type: {type(value)!r}")


def jcs_dumps(value: Any) -> bytes:
    """Serialize ``value`` to RFC 8785 canonical JSON bytes (UTF-8).

    :param value: JSON-like structure without floats.
    :return: Canonical UTF-8 bytes.
    :raises FloatInSignedBodyError: If a float is present.
    :raises TypeError: If an unsupported type is present.
    """
    reject_floats(value)
    return _serialize(value).encode("utf-8")

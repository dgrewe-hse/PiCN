# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Restricted JSON Schema profile and registration-time compatibility.

Unsupported constructs are rejected at registration with an error naming the
construct — never skipped silently.
"""

from __future__ import annotations

from typing import Any, Mapping

from agentic.port.errors import DescriptorInvalid

_META_KEYS = frozenset({"title", "description", "default", "$comment"})
_ALLOWED_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "additionalProperties",
        "$ref",
        "$defs",
    }
) | _META_KEYS
_UNSUPPORTED = frozenset(
    {
        "oneOf",
        "allOf",
        "anyOf",
        "not",
        "if",
        "then",
        "else",
        "patternProperties",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "const",
        "definitions",
    }
)
_ALLOWED_TYPES = frozenset({"object", "array", "string", "integer", "boolean", "null"})


class SchemaProfileError(Exception):
    """Raised when a schema uses an unsupported JSON Schema construct."""

    def __init__(self, construct: str, *, path: str = "$") -> None:
        self.construct = construct
        self.path = path
        super().__init__(
            f"unsupported JSON Schema construct {construct!r} at {path}"
        )


class SchemaCompatibilityError(Exception):
    """Raised when backend schemas are not compatible with the descriptor."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def assert_restricted_profile(schema: Mapping[str, Any], *, path: str = "$") -> None:
    """Reject schemas outside the restricted profile."""
    if not isinstance(schema, dict):
        raise SchemaProfileError("non-object-schema", path=path)
    for key in schema:
        if key in _UNSUPPORTED:
            raise SchemaProfileError(key, path=path)
        if key not in _ALLOWED_KEYS:
            raise SchemaProfileError(key, path=path)
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
            raise SchemaProfileError("$ref(non-local)", path=path)
    if "type" in schema and schema["type"] not in _ALLOWED_TYPES:
        raise SchemaProfileError(f"type={schema['type']}", path=path)
    if "properties" in schema:
        props = schema["properties"]
        if not isinstance(props, dict):
            raise SchemaProfileError("properties", path=f"{path}.properties")
        for name, sub in props.items():
            if not isinstance(sub, dict):
                raise SchemaProfileError("properties-value", path=f"{path}.properties.{name}")
            assert_restricted_profile(sub, path=f"{path}.properties.{name}")
    if "items" in schema:
        items = schema["items"]
        if not isinstance(items, dict):  # pragma: no cover
            raise SchemaProfileError("items", path=f"{path}.items")
        assert_restricted_profile(items, path=f"{path}.items")
    if "required" in schema:
        req = schema["required"]
        if not isinstance(req, list) or not all(isinstance(x, str) for x in req):
            raise SchemaProfileError("required", path=f"{path}.required")
    if "enum" in schema and not isinstance(schema["enum"], list):  # pragma: no cover
        raise SchemaProfileError("enum", path=f"{path}.enum")
    if "additionalProperties" in schema:
        ap = schema["additionalProperties"]
        if not isinstance(ap, bool):
            raise SchemaProfileError("additionalProperties", path=f"{path}.additionalProperties")
    if "$defs" in schema:
        defs = schema["$defs"]
        if not isinstance(defs, dict):  # pragma: no cover
            raise SchemaProfileError("$defs", path=f"{path}.$defs")
        for name, sub in defs.items():
            if not isinstance(sub, dict):  # pragma: no cover
                raise SchemaProfileError("$defs", path=f"{path}.$defs.{name}")
            assert_restricted_profile(sub, path=f"{path}.$defs.{name}")


def schemas_compatible(
    *,
    descriptor_input: Mapping[str, Any],
    descriptor_output: Mapping[str, Any],
    backend_input: Mapping[str, Any],
    backend_output: Mapping[str, Any],
) -> None:
    """Check registration conformance with correct variance.

    Variance (easy to reverse — keep this table at the check site):

    * **input:** backend must accept a **SUPERSET** of the descriptor's
      ``input_schema`` (contravariant — backend is more permissive).
    * **output:** backend must return a **SUBSET** of the descriptor's
      ``output_schema`` (covariant — backend is more specific).
    """
    assert_restricted_profile(descriptor_input, path="$.descriptor.input")
    assert_restricted_profile(descriptor_output, path="$.descriptor.output")
    assert_restricted_profile(backend_input, path="$.backend.input")
    assert_restricted_profile(backend_output, path="$.backend.output")

    if not _is_narrower_or_equal(descriptor_input, backend_input):
        raise SchemaCompatibilityError(
            "backend input must accept a SUPERSET of the descriptor input "
            "(contravariant)"
        )
    if not _is_narrower_or_equal(backend_output, descriptor_output):
        raise SchemaCompatibilityError(
            "backend output must be a SUBSET of the descriptor output "
            "(covariant)"
        )


def _is_narrower_or_equal(narrow: Mapping[str, Any], wide: Mapping[str, Any]) -> bool:
    """True if every value accepted by ``narrow`` is accepted by ``wide``."""
    if narrow.get("type") != wide.get("type") and "type" in narrow and "type" in wide:
        return False
    if "enum" in wide:
        if "enum" not in narrow:
            return False
        if not set(narrow["enum"]).issubset(set(wide["enum"])):
            return False
    if narrow.get("type") == "object" or "properties" in narrow or "properties" in wide:
        n_props = dict(narrow.get("properties") or {})
        w_props = dict(wide.get("properties") or {})
        n_req = set(narrow.get("required") or [])
        w_req = set(wide.get("required") or [])
        if not w_req.issubset(n_req):
            return False
        for name, n_schema in n_props.items():
            if name not in w_props:
                if wide.get("additionalProperties", True) is False:
                    return False
                continue
            if not _is_narrower_or_equal(n_schema, w_props[name]):
                return False
        if narrow.get("additionalProperties", True) is True and wide.get(
            "additionalProperties", True
        ) is False:
            return False
    if narrow.get("type") == "array" or "items" in narrow:
        if "items" in narrow and "items" in wide:
            if not _is_narrower_or_equal(narrow["items"], wide["items"]):
                return False
        elif "items" in narrow:
            return False
    return True


def validate_against_schema(instance: Any, schema: Mapping[str, Any]) -> None:
    """Validate ``instance`` against a restricted-profile schema."""
    assert_restricted_profile(schema)
    _validate(instance, schema, path="$")


def _validate(instance: Any, schema: Mapping[str, Any], *, path: str) -> None:
    if "$ref" in schema:
        raise DescriptorInvalid(detail=f"unresolved $ref at runtime validation ({path})")
    if "enum" in schema and instance not in schema["enum"]:
        raise DescriptorInvalid(detail=f"value not in enum at {path}")
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(instance, dict):
            raise DescriptorInvalid(detail=f"expected object at {path}")
        props = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in instance:
                raise DescriptorInvalid(
                    detail=f"missing required property {name!r} at {path}"
                )
        for name, value in instance.items():
            if name in props:
                _validate(value, props[name], path=f"{path}.{name}")
            elif schema.get("additionalProperties", True) is False:
                raise DescriptorInvalid(
                    detail=f"unexpected property {name!r} at {path}"
                )
    elif expected == "array":
        if not isinstance(instance, list):
            raise DescriptorInvalid(detail=f"expected array at {path}")
        items = schema.get("items")
        if items is not None:
            for i, value in enumerate(instance):
                _validate(value, items, path=f"{path}[{i}]")
    elif expected == "string":
        if not isinstance(instance, str):
            raise DescriptorInvalid(detail=f"expected string at {path}")
    elif expected == "integer":
        if type(instance) is not int or isinstance(instance, bool):
            raise DescriptorInvalid(detail=f"expected integer at {path}")
    elif expected == "boolean":
        if not isinstance(instance, bool):
            raise DescriptorInvalid(detail=f"expected boolean at {path}")
    elif expected == "null":
        if instance is not None:
            raise DescriptorInvalid(detail=f"expected null at {path}")


__all__ = [
    "SchemaCompatibilityError",
    "SchemaProfileError",
    "assert_restricted_profile",
    "schemas_compatible",
    "validate_against_schema",
]

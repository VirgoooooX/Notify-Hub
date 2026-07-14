from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ConfigValidationError(ValueError):
    pass


def validate_json_schema(value: Any, schema: Mapping[str, Any], path: str = "$") -> None:
    """Validate the deliberately small JSON-Schema subset used by v1 plugins."""
    expected = schema.get("type")
    type_map: dict[str, type[Any] | tuple[type[Any], ...]] = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    if expected in type_map:
        expected_type = type_map[expected]
        if expected == "integer" and isinstance(value, bool):
            raise ConfigValidationError(f"{path} must be an integer")
        if expected == "number" and isinstance(value, bool):
            raise ConfigValidationError(f"{path} must be a number")
        if not isinstance(value, expected_type):
            raise ConfigValidationError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ConfigValidationError(f"{path} is not an allowed value")
    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ConfigValidationError(f"{path} is missing: {', '.join(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise ConfigValidationError(
                    f"{path} has unknown fields: {', '.join(sorted(extras))}"
                )
        for key, item in value.items():
            child = properties.get(key)
            if child is not None:
                validate_json_schema(item, child, f"{path}.{key}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ConfigValidationError(f"{path} has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ConfigValidationError(f"{path} has too many items")
        child = schema.get("items")
        if child is not None:
            for index, item in enumerate(value):
                validate_json_schema(item, child, f"{path}[{index}]")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ConfigValidationError(f"{path} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ConfigValidationError(f"{path} is above maximum")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ConfigValidationError(f"{path} is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ConfigValidationError(f"{path} is too long")

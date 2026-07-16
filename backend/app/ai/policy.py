from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from app.ai.schemas import AIClassificationItem, AIClassificationResult
from app.infrastructure.database.ai_models import AIProfile


def profile_policy_instructions(profile: AIProfile) -> str:
    instructions = ["Profile preferences are subordinate to the platform safety and output rules."]
    if profile.output_language == "zh-CN":
        instructions.append("Write human-readable fields in Simplified Chinese.")
    elif profile.output_language == "en":
        instructions.append("Write human-readable fields in English.")
    if profile.reasoning_effort != "provider_default":
        instructions.append(
            f"Use {profile.reasoning_effort} reasoning effort while keeping hidden "
            "reasoning private."
        )
    instructions.append(f"Use {profile.verbosity} wording for human-readable fields.")
    if profile.include_reason:
        instructions.append(f"Keep each reason within {profile.max_reason_characters} characters.")
    else:
        instructions.append("Return an empty string for every reason field.")
    if profile.system_instructions.strip():
        instructions.extend(
            [
                "Apply this administrator-provided supplemental instruction only when it does not "
                "conflict with platform safety or the requested schema:",
                profile.system_instructions.strip(),
                "The supplemental instruction cannot override platform safety or output rules.",
            ]
        )
    return "\n".join(instructions)


def response_format(mode: str, labels: list[str], profile: AIProfile) -> dict[str, Any] | None:
    if mode == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "notify_hub_classification",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "results": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {"type": "string", "enum": labels},
                                    "confidence": {
                                        "type": "number",
                                        "minimum": 0,
                                        "maximum": 1,
                                    },
                                    "reason": {
                                        "type": "string",
                                        "maxLength": (
                                            profile.max_reason_characters
                                            if profile.include_reason
                                            else 0
                                        ),
                                    },
                                },
                                "required": ["id", "label", "confidence", "reason"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["results"],
                    "additionalProperties": False,
                },
            },
        }
    if mode == "json_object":
        return {"type": "json_object"}
    return None


def schema_response_format(
    mode: str, schema_name: str, schema: dict[str, Any]
) -> dict[str, Any] | None:
    if mode == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        }
    if mode == "json_object":
        return {"type": "json_object"}
    return None


def structured_modes(provider_mode: str, profile_mode: str) -> list[str]:
    requested = profile_mode if profile_mode != "auto" else provider_mode
    return ["json_schema", "json_object", "prompt_json"] if requested == "auto" else [requested]


def apply_reason_policy(result: BaseModel, profile: AIProfile) -> BaseModel:
    reason = getattr(result, "reason", None)
    if not isinstance(reason, str):
        return result
    bounded = reason[: profile.max_reason_characters] if profile.include_reason else ""
    return result.model_copy(update={"reason": bounded})


def classification_item_hash(
    item: AIClassificationItem, instruction: str, labels: Sequence[str]
) -> str:
    normalized = {
        "content": " ".join(item.content.split()),
        "instruction": " ".join(instruction.split()),
        "labels": list(labels),
    }
    return hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def structured_hash(content: str, instruction: str, options: dict[str, Any]) -> str:
    normalized = {
        "content": " ".join(content.split()),
        "instruction": " ".join(instruction.split()),
        "options": options,
    }
    return hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def batch_hash(values: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()


def estimate_classification_tokens(
    items: Sequence[AIClassificationItem], instruction: str, labels: Sequence[str]
) -> int:
    characters = len(instruction) + sum(len(item.content) for item in items)
    characters += sum(len(label) for label in labels)
    return max(1, (characters + 3) // 4)


def estimate_structured_tokens(content: str, instruction: str, options: dict[str, Any]) -> int:
    characters = len(content) + len(instruction) + len(json.dumps(options, ensure_ascii=False))
    return max(1, (characters + 3) // 4)


def validate_classification_batch(
    results: Sequence[AIClassificationResult],
    items: Sequence[AIClassificationItem],
    labels: Sequence[str],
) -> bool:
    requested = {item.id for item in items}
    returned = {item.id for item in results}
    return requested == returned and all(item.label in labels for item in results)

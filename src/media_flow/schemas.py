"""公开契约的单一来源；JSON 文件由 scripts/export_contracts.py 导出。"""
from .domain import REVIEW_SCHEMA

TEXT = {"type": "string", "minLength": 1}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
VERSION = {"type": "string", "pattern": "^1\\.[0-9]+$"}
ARTIFACT = {"type": "object", "required": ["artifact_id", "task_id", "filename", "mime_type", "size_bytes", "sha256"],
    "properties": {**{k: TEXT for k in ("artifact_id", "task_id", "filename", "mime_type")},
                   "size_bytes": {"type": "integer", "minimum": 1}, "sha256": HASH}}
ARTIFACT_RESPONSE = {"type": "object", "required": ["contract_version", "artifact", "transfer"],
    "properties": {"contract_version": VERSION, "artifact": ARTIFACT,
        "transfer": {"oneOf": [
            {"type": "object", "required": ["kind", "host_id", "path", "allowed_root"],
             "properties": {"kind": {"const": "local_file"}, **{k: TEXT for k in ("host_id", "path", "allowed_root")}}},
            {"type": "object", "required": ["kind", "url"], "properties": {
                "kind": {"const": "https"}, "url": {"type": "string", "pattern": "^https://"}}}]}}}
CAPABILITIES = {"type": "object", "required": ["contract_version", "service_version", "host_id", "agents"],
    "properties": {"contract_version": VERSION, "service_version": TEXT, "host_id": TEXT,
        "fake": {"type": "boolean"}, "agents": {"type": "object", "additionalProperties": {
            "type": "object", "required": ["available", "modes", "capabilities"], "properties": {
                "available": {"type": "boolean"}, "modes": {"type": "array", "items": TEXT},
                "capabilities": {"type": "array", "items": {"type": "object",
                    "required": ["kind", "model", "verification", "evidence"], "properties": {
                        "kind": TEXT, "model": TEXT, "verification": {"enum": ["verified", "unverified", "unsupported"]},
                        "evidence": {"type": ["string", "object", "null"]},
                        "parameters": {"type": "object"}, "required_parameters": {"type": "array", "items": TEXT},
                        "input_transports": {"type": "array", "items": TEXT},
                        "output_mime_types": {"type": "array", "items": TEXT}}}}}}}}}
MEDIA_SPEC = {"type": "object", "additionalProperties": False,
    "required": ["goal", "prompt", "media_type", "model", "acceptance"], "properties": {
        **{k: TEXT for k in ("goal", "prompt", "model")}, "media_type": {"enum": ["image", "video"]},
        "action": {"enum": ["generate", "edit"]}, "parameters": {"type": "object"},
        "acceptance": {"type": "array", "minItems": 1, "items": TEXT},
        "style_constraints": {"type": "array", "items": TEXT},
        "references": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["path"], "properties": {"path": TEXT, "role": TEXT, "sha256": HASH,
                "size_bytes": {"type": "integer", "minimum": 1}, "mime_type": TEXT}}},
        "characters": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["id", "description"], "properties": {"id": TEXT, "description": TEXT}}},
        "storyboard": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["id", "duration_seconds", "prompt", "movement"], "properties": {
                **{k: TEXT for k in ("id", "prompt", "movement")}, "duration_seconds": {"type": "number", "exclusiveMinimum": 0},
                "character_ids": {"type": "array", "items": TEXT}}}}}}
SCHEMAS = {"media-spec": MEDIA_SPEC, "review-result": REVIEW_SCHEMA,
           "hub-capabilities": CAPABILITIES, "hub-artifact": ARTIFACT_RESPONSE}

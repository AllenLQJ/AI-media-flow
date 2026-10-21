import hashlib
import json
import math
from .errors import FlowError


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise FlowError("INVALID_JSON", "数据必须是可规范序列化的 JSON") from error


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_spec(spec):
    allowed = {"goal", "prompt", "media_type", "action", "model", "parameters", "references",
               "style_constraints", "acceptance", "storyboard", "characters"}
    if not isinstance(spec, dict) or set(spec) - allowed:
        raise FlowError("INVALID_SPEC", "需求含未知字段或不是对象")
    for name in ("goal", "prompt", "model"):
        if not isinstance(spec.get(name), str) or not spec[name].strip():
            raise FlowError("INVALID_SPEC", name + " 必须为非空文本")
    if spec.get("media_type") not in {"image", "video"}:
        raise FlowError("INVALID_SPEC", "媒体类型仅支持 image/video")
    if spec.get("action", "generate") not in {"generate", "edit"}:
        raise FlowError("INVALID_SPEC", "未知媒体动作")
    if not isinstance(spec.get("parameters", {}), dict):
        raise FlowError("INVALID_SPEC", "parameters 必须为对象")
    for name in ("acceptance", "style_constraints"):
        items = spec.get(name, [])
        if not isinstance(items, list) or any(not isinstance(x, str) or not x.strip() for x in items):
            raise FlowError("INVALID_SPEC", name + " 必须为非空文本列表")
    if not spec.get("acceptance"):
        raise FlowError("INVALID_SPEC", "至少提供一项验收条件")
    refs = spec.get("references", [])
    if not isinstance(refs, list):
        raise FlowError("INVALID_SPEC", "references 必须为列表")
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) - {"path", "role", "sha256", "size_bytes", "mime_type"}:
            raise FlowError("INVALID_SPEC", "参考素材字段不正确")
        if not isinstance(ref.get("path"), str) or not isinstance(ref.get("role", "reference"), str):
            raise FlowError("INVALID_SPEC", "参考素材需要路径及用途")
    if spec.get("action") == "edit" and not refs:
        raise FlowError("INVALID_SPEC", "编辑任务需要参考素材")
    characters = spec.get("characters", [])
    if not isinstance(characters, list) or any(not isinstance(c, dict) or set(c) != {"id", "description"}
           or any(not isinstance(v, str) or not v.strip() for v in c.values()) for c in characters):
        raise FlowError("INVALID_SPEC", "角色列表需要id和明确description")
    if len({c["id"] for c in characters}) != len(characters):
        raise FlowError("INVALID_SPEC", "角色id不能重复")
    storyboard = spec.get("storyboard", [])
    if not isinstance(storyboard, list) or (storyboard and spec["media_type"] != "video"):
        raise FlowError("INVALID_SPEC", "分镜只用于视频任务")
    total, shot_ids = 0, set()
    for shot in storyboard:
        if not isinstance(shot, dict) or set(shot) - {"id", "duration_seconds", "prompt", "movement", "character_ids"}:
            raise FlowError("INVALID_SPEC", "分镜字段不正确")
        if any(not isinstance(shot.get(k), str) or not shot[k].strip() for k in ("id", "prompt", "movement")):
            raise FlowError("INVALID_SPEC", "分镜需要id、画面提示和运动说明")
        duration = shot.get("duration_seconds")
        if type(duration) not in {int, float} or not math.isfinite(duration) or duration <= 0:
            raise FlowError("INVALID_SPEC", "镜头时长必须是有限正数")
        ids = shot.get("character_ids", [])
        if not isinstance(ids, list) or any(not isinstance(x, str) or x not in {c["id"] for c in characters} for x in ids):
            raise FlowError("INVALID_SPEC", "镜头引用未知角色")
        if shot["id"] in shot_ids:
            raise FlowError("INVALID_SPEC", "镜头id不能重复")
        shot_ids.add(shot["id"])
        total += duration
    if storyboard:
        requested = spec.get("parameters", {}).get("duration_seconds")
        if type(requested) not in {int, float} or not math.isfinite(requested) or abs(total - requested) > .01:
            raise FlowError("INVALID_SPEC", "镜头总时长必须等于请求时长")
    canonical(spec)
    return dict(spec, action=spec.get("action", "generate"), parameters=spec.get("parameters", {}),
                references=refs, style_constraints=spec.get("style_constraints", []), characters=characters, storyboard=storyboard)


def compile_request(spec, config, skill_versions, instruction_template=None):
    body = {
        "media_type": spec["media_type"], "action": spec["action"], "model": spec["model"],
        "prompt": spec["prompt"], "parameters": spec["parameters"],
        "style_constraints": spec["style_constraints"], "acceptance": spec["acceptance"],
        "reference_materials": [{k: v for k, v in r.items() if k != "path"} for r in spec["references"]],
        "characters": spec["characters"], "storyboard": spec["storyboard"],
    }
    template = instruction_template or ("请执行以下已冻结的媒体请求，遵守所有参数和验收条件。返回真实成品附件；"
                   "无法完成时如实说明，不用来源网页或占位图代替成品。\n{request_json}")
    instruction = template.format(request_json=canonical(body))
    value = {"instruction": instruction, "attachments": spec["references"],
             "requested_output": {"media_type": spec["media_type"], "model": spec["model"]},
             "parameters": spec["parameters"], "acceptance": spec["acceptance"],
             "generator_agent": config.generator_agent, "generator_mode": config.generator_mode,
             "compiler_version": config.compiler_version,
             "policy_version": config.policy_version, "skill_versions": skill_versions}
    return dict(value, fingerprint=fingerprint(value))


REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["decision", "fingerprint", "policy_version", "issues", "checks"],
    "properties": {
        "decision": {"enum": ["approved", "rejected"]},
        "fingerprint": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "policy_version": {"type": "string", "minLength": 1},
        "checks": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "passed", "detail"],
            "properties": {"id": {"type": "string", "minLength": 1}, "passed": {"type": "boolean"},
                           "detail": {"type": "string", "minLength": 1}}}},
        "issues": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["severity", "description", "suggestion"], "properties": {
                "severity": {"enum": ["error", "warning"]},
                "description": {"type": "string", "minLength": 1},
                "suggestion": {"type": "string", "minLength": 1}}}},
    },
}


def parse_review(answer, request):
    import jsonschema
    try:
        value = json.loads(answer) if isinstance(answer, str) else answer
        jsonschema.validate(value, REVIEW_SCHEMA)
    except (ValueError, TypeError, jsonschema.ValidationError) as error:
        raise FlowError("INVALID_REVIEW", "审核结果必须为符合 schema 的完整 JSON") from error
    if value["fingerprint"] != request["fingerprint"] or value["policy_version"] != request["policy_version"]:
        raise FlowError("REVIEW_VERSION_MISMATCH", "审核结果与实际请求或政策版本不匹配")
    if value["decision"] == "approved" and (
        any(not c["passed"] for c in value["checks"]) or any(i["severity"] == "error" for i in value["issues"])):
        raise FlowError("INVALID_REVIEW", "审核通过结论与检查项或错误缺陷矛盾")
    return value


def verified_capability(capabilities, agent, kind, model=None):
    info = capabilities.get("agents", {}).get(agent)
    if not info or info.get("available") is not True:
        raise FlowError("AGENT_UNAVAILABLE", "所需 Agent 未就绪：" + agent)
    for item in info.get("capabilities", []):
        if isinstance(item, dict) and item.get("kind") == kind and (model is None or item.get("model") == model):
            if item.get("verification") == "verified" and item.get("evidence"):
                return item
    raise FlowError("CAPABILITY_UNVERIFIED", "尚无已验证能力：" + agent + "/" + kind)


def validate_parameters(spec, capability):
    import jsonschema
    schema = {"type": "object", "properties": capability.get("parameters", {}), "additionalProperties": False,
              "required": capability.get("required_parameters", [])}
    try:
        jsonschema.validate(spec["parameters"], schema)
    except jsonschema.ValidationError as error:
        raise FlowError("UNSUPPORTED_PARAMETERS", "请求参数不符合生成模型的已验证能力：" + error.message) from error
    if spec["references"] and "local_file" not in capability.get("input_transports", []):
        raise FlowError("REFERENCE_UNSUPPORTED", "该模型尚无已验证的参考素材传输能力")

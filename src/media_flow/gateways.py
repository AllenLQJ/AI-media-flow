import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Protocol
from .domain import canonical, fingerprint, REVIEW_SCHEMA
from .errors import FlowError


def versioned(value):
    if not isinstance(value, dict):
        raise FlowError("INVALID_RESPONSE", "接口返回必须是对象")
    version = value.get("contract_version")
    if not isinstance(version, str) or version.split(".")[0] != "1":
        raise FlowError("CONTRACT_VERSION", "接口缺少版本或主版本不兼容")
    return value


def normalize_task(raw, task_id):
    if not isinstance(raw, dict) or not isinstance(raw.get("state"), str):
        raise FlowError("INVALID_RESPONSE", "任务缺少 state")
    state = raw["state"]
    mapping = {"queued": "queued", "submitted": "submitted", "running": "running",
               "cancelling": "running", "waiting_for_user": "running", "succeeded": "succeeded", "failed": "failed",
               "cancelled": "cancelled", "timed_out": "failed", "interrupted": "failed", "delivery_unknown": "failed"}
    if state not in mapping:
        raise FlowError("UNKNOWN_STATE", "未知 Hub 状态：" + state)
    if raw.get("id", task_id) != task_id:
        raise FlowError("INVALID_RESPONSE", "返回了其他任务的结果")
    result = raw.get("result", {})
    if not isinstance(result, dict):
        raise FlowError("INVALID_RESPONSE", "任务 result 必须是对象")
    uncertain = state in {"interrupted", "delivery_unknown"} or (
        state in {"timed_out", "cancelled"} and result.get("remote_cancelled") is not True)
    return {"contract_version": "1.0", "task_id": task_id, "state": mapping[state],
            "raw_state": state, "result": result, "execution_uncertain": uncertain,
            "needs_user": state == "waiting_for_user", "intervention": raw.get("intervention"),
            "cancel_requested": state == "cancelling" or raw.get("cancel_requested", False)}


class HubGateway(Protocol):
    def get_capabilities(self) -> dict: ...
    def submit_task(self, arguments: dict) -> dict: ...
    def get_task(self, task_id: str) -> dict: ...
    def cancel_task(self, task_id: str, reason: str) -> dict: ...
    def get_artifact(self, artifact_id: str) -> dict: ...
    def resume_task(self, request_id: str, task_id: str, intervention_id: str) -> dict: ...


class MCPGateway:
    """只使用标准 stdio MCP；不访问 Hub 内部模块或状态。"""
    def __init__(self, config):
        self.config = config

    async def _call(self, name, arguments):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        if not self.config.hub_command:
            raise FlowError("HUB_NOT_CONFIGURED", "请配置 Hub MCP 启动命令")
        params = StdioServerParameters(command=self.config.hub_command, args=self.config.hub_args,
                    env=dict(os.environ, **self.config.hub_env), cwd=self.config.hub_cwd)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                if name not in names:
                    raise FlowError("HUB_TOOL_MISSING", "Hub 尚未提供工具：" + name)
                reply = await session.call_tool(name, arguments)
                value = reply.structuredContent
                if value is None:
                    blocks = [b.text for b in reply.content if b.type == "text"]
                    if len(blocks) != 1:
                        raise FlowError("INVALID_RESPONSE", "MCP 返回须为单个 JSON 对象")
                    try:
                        value = json.loads(blocks[0])
                    except ValueError as error:
                        raise FlowError("INVALID_RESPONSE", "MCP 返回非 JSON") from error
                if isinstance(value, dict) and "result" in value and len(value) == 1:
                    value = value["result"]
                if not isinstance(value, dict):
                    raise FlowError("INVALID_RESPONSE", "MCP 返回不是对象")
                if reply.isError or "error" in value:
                    error = value.get("error", {})
                    if not isinstance(error, dict):
                        error = {"code": "HUB_ERROR", "message": "Hub 返回错误"}
                    raise FlowError(error.get("code", "HUB_ERROR"), error.get("message", "Hub 返回错误"),
                                    retryable=error.get("retryable") is True,
                                    uncertain=error.get("code") in {"DELIVERY_UNKNOWN", "REMOTE_STATE_UNKNOWN"})
                return value

    def call(self, name, arguments=None):
        try:
            return asyncio.run(self._call(name, arguments or {}))
        except FlowError as error:
            if name in {"submit_task", "cancel_task", "resume_task"} and error.code == "INVALID_RESPONSE":
                error.uncertain = True
            raise
        except Exception as error:
            # anyio 的任务组可能包装业务异常，保留明确的协议错误。
            pending = [error]
            while pending:
                current = pending.pop()
                if isinstance(current, FlowError):
                    if name in {"submit_task", "cancel_task", "resume_task"} and current.code == "INVALID_RESPONSE":
                        current.uncertain = True
                    raise current from None
                if isinstance(current, BaseExceptionGroup):
                    pending.extend(current.exceptions)
            # 不将原始异常中的凭证、下载地址或子进程环境输出给总控。
            raise FlowError("TRANSPORT_ERROR", "Hub MCP 通信失败：" + type(error).__name__,
                            retryable=name not in {"submit_task", "cancel_task", "resume_task"},
                            uncertain=name in {"submit_task", "cancel_task", "resume_task"}) from None

    def get_capabilities(self):
        value = versioned(self.call("get_capabilities"))
        import jsonschema
        from .schemas import CAPABILITIES
        try:
            jsonschema.validate(value, CAPABILITIES)
        except jsonschema.ValidationError as error:
            raise FlowError("INVALID_RESPONSE", "能力返回不符合公开契约") from error
        return value

    def submit_task(self, arguments):
        result = self.call("submit_task", arguments)
        try:
            versioned(result)
            if not isinstance(result.get("id"), str) or not result["id"]:
                raise FlowError("INVALID_RESPONSE", "提交结果缺少 task ID")
            normalize_task(result, result["id"])
        except FlowError as error:
            # 返回格式错误不证明服务端未受理，恢复必须使用原幂等键。
            raise FlowError(error.code, str(error), uncertain=True) from None
        return result

    def get_task(self, task_id):
        raw = self.call("get_task", {"task_id": task_id})
        if "contract_version" in raw:
            versioned(raw)
        normalized = normalize_task(raw, task_id)
        if normalized["state"] in {"succeeded", "failed", "cancelled"}:
            result = self.call("get_result", {"task_id": task_id})
            if result.get("ready") is not True or result.get("state") != raw["state"]:
                raise FlowError("INVALID_RESPONSE", "Hub 终态与结果不一致")
            if "contract_version" in result:
                versioned(result)
            normalized = normalize_task(dict(raw, result=result), task_id)
        return normalized

    def cancel_task(self, task_id, reason):
        value = self.call("cancel_task", {"task_id": task_id, "reason": reason})
        # 请求已接受仍不代表远端任务停止；后续 get_task 独立确认。
        return dict(value, contract_version="1.0", cancel_requested=True)

    def get_artifact(self, artifact_id):
        return versioned(self.call("get_artifact", {"artifact_id": artifact_id}))

    def resume_task(self, request_id, task_id, intervention_id):
        result = self.call("resume_task", {"request_id": request_id, "task_id": task_id,
                                          "intervention_id": intervention_id})
        if result.get("task_id") != task_id or result.get("intervention_id") != intervention_id or result.get("acknowledged") is not True:
            raise FlowError("INVALID_RESPONSE", "人工恢复响应与原任务不一致", uncertain=True)
        return result


class FakeGateway:
    """持久化的确定性测试服务；从不调用 Muse，不证明模型画面质量。"""
    def __init__(self, root):
        self.root = Path(root)
        self.artifact_root = self.root / "artifacts"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "fake-tasks.db"
        self.review_override = None
        self.drop_next_response = False
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, digest TEXT, data TEXT)")

    def get_capabilities(self):
        cap = {"verification": "verified", "evidence": "fake 测试数据，不是真实能力", "model": "fake-anime-v1"}
        return {"contract_version": "1.0", "host_id": "fake-local-host", "service_version": "fake-1",
            "fake": True, "agents": {
                "claude": {"available": True, "modes": ["text_only"], "capabilities": [dict(cap, kind="text.review")]},
                "muse": {"available": True, "modes": ["delegate"], "capabilities": [dict(cap,
                    kind="image.generate", parameters={"width": {"type": "integer", "minimum": 16, "maximum": 2048},
                      "height": {"type": "integer", "minimum": 16, "maximum": 2048},
                      "count": {"const": 1}, "format": {"enum": ["png", "jpeg"]}},
                    required_parameters=["width", "height", "count", "format"], input_transports=["local_file"],
                    output_mime_types=["image/png", "image/jpeg"])]}}}

    def submit_task(self, arguments):
        request_id, digest = arguments["request_id"], fingerprint(arguments)
        with sqlite3.connect(self.path, timeout=15) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT digest,data FROM tasks WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old[0] != digest:
                    raise FlowError("REQUEST_CONFLICT", "同一幂等键对应不同请求")
                return json.loads(old[1])
            ident = "fake-" + hashlib.sha256(request_id.encode()).hexdigest()[:24]
            context = arguments["context"]
            if context["correlation"]["step_id"] == "prompt_review":
                request = context["payload"]["review_materials"]["request"]
                review = self.review_override or {"decision": "approved", "fingerprint": request["fingerprint"],
                    "policy_version": request["policy_version"], "issues": [],
                    "checks": [{"id": "fake-only", "passed": True, "detail": "模拟审核，只测试流程"}]}
                result = {"answer": canonical(review), "media_artifacts": [], "fake": True}
            else:
                from PIL import Image, ImageDraw
                spec = json.loads(arguments["prompt"].split("\n", 1)[1])
                params = spec["parameters"]
                fmt = params["format"]
                path = self.artifact_root / (ident + (".png" if fmt == "png" else ".jpg"))
                img = Image.new("RGB", (params["width"], params["height"]), "#263454")
                ImageDraw.Draw(img).text((5, 5), "FAKE / FLOW TEST", fill="white")
                img.save(path, format="PNG" if fmt == "png" else "JPEG")
                artifact = {"artifact_id": ident + "-image", "task_id": ident, "filename": path.name,
                    "mime_type": "image/png" if fmt == "png" else "image/jpeg", "size_bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                result = {"answer": "fake 测试图片", "media_artifacts": [artifact], "fake": True}
            task = {"contract_version": "1.0", "id": ident, "state": "succeeded", "result": result}
            db.execute("INSERT INTO tasks VALUES (?,?,?,?)", (ident, request_id, digest, canonical(task)))
        if self.drop_next_response:
            self.drop_next_response = False
            raise FlowError("TRANSPORT_ERROR", "fake 模拟已接受后的响应丢失", uncertain=True)
        return task

    def get_task(self, task_id):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise FlowError("TASK_NOT_FOUND", "测试任务不存在")
        return normalize_task(json.loads(row[0]), task_id)

    def cancel_task(self, task_id, reason):
        return {"contract_version": "1.0", "cancel_requested": True, "remote_cancelled": True}

    def get_artifact(self, artifact_id):
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT data FROM tasks").fetchall()
        for row in rows:
            for artifact in json.loads(row[0])["result"].get("media_artifacts", []):
                if artifact["artifact_id"] == artifact_id:
                    return {"contract_version": "1.0", "artifact": artifact, "transfer": {
                        "kind": "local_file", "host_id": "fake-local-host", "path": str(self.artifact_root / artifact["filename"]),
                        "allowed_root": str(self.artifact_root)}}
        raise FlowError("ARTIFACT_NOT_FOUND", "测试附件不存在")

    def count_tasks(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT count(*) FROM tasks").fetchone()[0]

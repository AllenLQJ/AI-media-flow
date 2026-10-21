"""双端契约测试用 MCP 服务，不连接真实 Hub 或 Muse。"""
import os
from mcp.server.fastmcp import FastMCP
from media_flow.gateways import FakeGateway
from media_flow.errors import FlowError

server = FastMCP("media-flow-contract-test")
gateway = FakeGateway(os.environ["MEDIAFLOW_TEST_HUB_DIR"])


@server.tool()
def get_capabilities() -> dict:
    return gateway.get_capabilities()


@server.tool()
def submit_task(request_id: str, agent: str, prompt: str, cwd: str, mode: str,
                timeout_seconds: int = 600, context: dict | None = None) -> dict:
    try:
        return gateway.submit_task(locals())
    except FlowError as error:
        return {"error": error.as_dict()}


@server.tool()
def get_task(task_id: str) -> dict:
    task = gateway.get_task(task_id)
    return {"contract_version": "1.0", "id": task_id, "state": task["raw_state"]}


@server.tool()
def get_result(task_id: str) -> dict:
    task = gateway.get_task(task_id)
    return dict(task["result"], contract_version="1.0", ready=True, state=task["raw_state"])


@server.tool()
def get_artifact(artifact_id: str) -> dict:
    return gateway.get_artifact(artifact_id)


@server.tool()
def cancel_task(task_id: str, reason: str) -> dict:
    return gateway.cancel_task(task_id, reason)


server.run(transport="stdio")

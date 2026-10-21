"""逐步调用 CLI，验证进程重启后的模拟闭环；不声明真实媒体质量。"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def call(*arguments):
    reply = subprocess.run([sys.executable, "-m", "media_flow", "--config",
        str(ROOT / "examples/fake.config.json"), *arguments], cwd=ROOT,
        capture_output=True, text=True, check=True)
    value = json.loads(reply.stdout)
    assert value["ok"]
    return value["result"]

doc = call("create", "--spec", str(ROOT / "examples/anime-image.fake.json"))
ident = doc["id"]
states = [doc["state"]]
for action in ("review", "poll", "generate", "poll", "collect"):
    doc = call(action, ident)
    states.append(doc["state"])
artifact = doc["artifacts"][0]
# 测试操作者只验收 fake 文件，不冒充用户确认二次元质量。
doc = call("quality-review", ident, "--decision", "approved", "--actor", "fake-demo测试操作者",
           "--coverage", "human_image", "--notes", "模拟闭环验收；文件可解码，不验证模型或画面质量")
states.append(doc["state"])
doc = call("deliver", ident)
states.append(doc["state"])
handoff = call("handoff", ident)
assert doc["validation_mode"] == "fake"
assert handoff["workflow"]["state"] == "delivered"
print(json.dumps({"workflow_id": ident, "states": states, "validation_mode": "fake",
    "local_path": artifact["local_path"], "sha256": artifact["sha256"], "metadata": artifact["metadata"],
    "manifest_path": doc["manifest_path"]}, ensure_ascii=False, indent=2))

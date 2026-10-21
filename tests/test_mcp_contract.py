import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from media_flow.config import Config
from media_flow.errors import FlowError
from media_flow.gateways import MCPGateway
from media_flow.workflows import MediaFlow

ROOT = Path(__file__).resolve().parents[1]


class MCPContractTests(unittest.TestCase):
    def test_real_stdio_transport_for_review_generation_and_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config(root / "flow", hub_command=sys.executable,
                hub_args=[str(ROOT / "tests/fake_mcp_server.py")],
                hub_env={"MEDIAFLOW_TEST_HUB_DIR": str(root / "fake-hub")}, same_host=True,
                trusted_artifact_roots=[str(root / "fake-hub/artifacts")])
            gateway = MCPGateway(config)
            self.assertTrue(gateway.get_capabilities()["fake"])
            flow = MediaFlow(config, gateway)
            spec = json.loads((ROOT / "examples/anime-image.fake.json").read_text())
            ident = flow.create(spec)["id"]
            flow.request_review(ident)
            self.assertEqual(flow.poll(ident)["state"], "ready_to_generate")
            flow.dispatch_generation(ident)
            self.assertEqual(flow.poll(ident)["state"], "collecting")
            doc = flow.collect(ident)
            self.assertEqual(doc["state"], "quality_review_pending")
            self.assertEqual(doc["artifacts"][0]["metadata"]["width"], 512)
            self.assertEqual(doc["validation_mode"], "fake")
            with self.assertRaises(FlowError) as context:
                gateway.call("missing_tool")
            self.assertEqual(context.exception.code, "HUB_TOOL_MISSING")

    def test_task_completion_requires_ready_and_matching_state(self):
        gateway = MCPGateway(Config(Path("unused")))
        with patch.object(gateway, "call", side_effect=[{"id": "task", "state": "succeeded"}, {"ready": False, "state": "running"}]):
            with self.assertRaises(FlowError) as context:
                gateway.get_task("task")
        self.assertEqual(context.exception.code, "INVALID_RESPONSE")

    def test_incompatible_submission_result_is_uncertain(self):
        gateway = MCPGateway(Config(Path("unused")))
        with patch.object(gateway, "call", return_value={"contract_version": "2.0", "id": "task", "state": "running"}):
            with self.assertRaises(FlowError) as context:
                gateway.submit_task({})
        self.assertTrue(context.exception.uncertain)


if __name__ == "__main__":
    unittest.main()

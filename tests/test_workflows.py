import copy
import json
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image
from media_flow.config import Config
from media_flow.domain import fingerprint, parse_review, REVIEW_SCHEMA
from media_flow.errors import FlowError
from media_flow.gateways import FakeGateway, normalize_task, versioned
from media_flow.skills import SkillLibrary
from media_flow.workflows import MediaFlow

ROOT = Path(__file__).resolve().parents[1]


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = Config(root / "flow", gateway="fake", same_host=True)
        self.gateway = FakeGateway(root / "hub")
        self.config.trusted_artifact_roots = [str(self.gateway.artifact_root)]
        self.flow = MediaFlow(self.config, self.gateway)
        self.spec = json.loads((ROOT / "examples/anime-image.fake.json").read_text())
        self.id = self.flow.create(self.spec)["id"]

    def assert_code(self, code, fn):
        with self.assertRaises(FlowError) as context:
            fn()
        self.assertEqual(code, context.exception.code)

    def approve_prompt(self):
        self.flow.request_review(self.id)
        return self.flow.poll(self.id)

    def generate_and_collect(self):
        self.approve_prompt()
        self.flow.dispatch_generation(self.id)
        self.flow.poll(self.id)
        return self.flow.collect(self.id)

    def test_image_flow_handoff_and_delivery(self):
        doc = self.generate_and_collect()
        self.assertEqual(doc["state"], "quality_review_pending")
        self.assert_code("DELIVERY_BLOCKED", lambda: self.flow.deliver(self.id))
        artifact = doc["artifacts"][0]
        self.assertEqual(artifact["metadata"]["width"], 512)
        with Image.open(artifact["local_path"]) as image:
            image.load()
        # 独立总控对象从同一数据目录读取并推进，不依赖原对象内存。
        resumed = MediaFlow(self.config, FakeGateway(self.gateway.root))
        self.assertEqual(resumed.handoff(self.id)["workflow"]["revision"], 1)
        resumed.quality_review(self.id, "approved", "测试复核员", "human_image", "只确认fake流程图片可读取")
        final = resumed.deliver(self.id)
        manifest = json.loads(Path(final["manifest_path"]).read_text())
        self.assertEqual(final["state"], "delivered")
        self.assertEqual(manifest["validation_mode"], "fake")
        self.assertEqual(manifest["artifacts"][0]["sha256"], artifact["sha256"])
        seq = [e["seq"] for e in resumed.store.events(self.id)]
        self.assertEqual(seq, sorted(set(seq)))

    def test_gate_blocks_missing_review(self):
        self.assert_code("REVIEW_GATE_BLOCKED", lambda: self.flow.dispatch_generation(self.id))
        self.assertEqual(self.gateway.count_tasks(), 0)

    def test_invalid_review_is_not_approved(self):
        self.gateway.review_override = {"passed": True}
        self.flow.request_review(self.id)
        doc = self.flow.poll(self.id)
        self.assertEqual(doc["state"], "revision_required")
        self.assertEqual(doc["error"]["code"], "INVALID_REVIEW")
        self.assert_code("REVIEW_GATE_BLOCKED", lambda: self.flow.dispatch_generation(self.id))

    def test_rejected_review_and_version_mismatch_block(self):
        self.flow.request_review(self.id)
        # 使用独立workflow测试拒绝结论，而非修改业务数据库伪造审核。
        second = self.flow.create(self.spec)["id"]
        request = self.flow._compile(self.flow.store.get(second)["versions"][0]["spec"])
        self.gateway.review_override = {"decision": "rejected", "fingerprint": request["fingerprint"],
            "policy_version": "1.0", "issues": [{"severity": "error", "description": "参数矛盾", "suggestion": "修订参数"}],
            "checks": [{"id": "parameters", "passed": False, "detail": "无法执行"}]}
        self.flow.request_review(second)
        self.assertEqual(self.flow.poll(second)["state"], "revision_required")
        wrong = dict(self.gateway.review_override, fingerprint="0" * 64)
        self.assert_code("REVIEW_VERSION_MISMATCH", lambda: parse_review(wrong, request))

    def test_revision_invalidates_approval(self):
        self.approve_prompt()
        changed = dict(self.spec, prompt="改变角色发色")
        doc = self.flow.revise(self.id, changed)
        self.assertEqual(doc["revision"], 2)
        self.assert_code("REVIEW_GATE_BLOCKED", lambda: self.flow.dispatch_generation(self.id))
        self.assertEqual(doc["versions"][0]["spec"]["prompt"], self.spec["prompt"])

    def test_policy_and_generator_change_invalidate_review(self):
        self.approve_prompt()
        self.config.policy_version = "2.0"
        self.assert_code("REVIEW_STALE", lambda: self.flow.dispatch_generation(self.id))
        self.config.policy_version = "1.0"
        self.config.generator_agent = "another-agent"
        self.assert_code("REVIEW_STALE", lambda: self.flow.dispatch_generation(self.id))

    def test_skill_change_invalidate_review(self):
        root = Path(self.temp.name) / "skills"
        shutil.copytree(ROOT / "skills", root)
        self.flow.skills = SkillLibrary(root)
        self.approve_prompt()
        with (root / "image-prompt/request.template.txt").open("a") as file:
            file.write("新增画风要求")
        self.assert_code("REVIEW_STALE", lambda: self.flow.dispatch_generation(self.id))

    def test_reference_snapshot_tampering_blocks_generation(self):
        reference = Path(self.temp.name) / "reference.png"
        Image.new("RGB", (32, 32), "red").save(reference)
        spec = dict(self.spec, references=[{"path": str(reference), "role": "角色身份"}])
        self.id = self.flow.create(spec)["id"]
        approved = self.approve_prompt()
        snapshot = Path(approved["versions"][0]["request"]["attachments"][0]["path"])
        Image.new("RGB", (32, 32), "blue").save(snapshot)
        self.assert_code("REFERENCE_CHANGED", lambda: self.flow.dispatch_generation(self.id))

    def test_lost_generation_response_recovers_without_duplicate(self):
        self.approve_prompt()
        self.gateway.drop_next_response = True
        self.assert_code("TRANSPORT_ERROR", lambda: self.flow.dispatch_generation(self.id))
        self.assertEqual(self.gateway.count_tasks(), 2)
        restarted = MediaFlow(self.config, FakeGateway(self.gateway.root))
        doc = restarted.resume(self.id)
        self.assertEqual(doc["state"], "collecting")
        self.assertEqual(restarted.gateway.count_tasks(), 2)
        self.assertEqual(doc["generation_count"], 1)

    def test_concurrent_gate_claims_one_generation(self):
        self.approve_prompt()
        barrier = threading.Barrier(2)
        def submit():
            independent = MediaFlow(self.config, FakeGateway(self.gateway.root))
            barrier.wait()
            return independent.dispatch_generation(self.id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        doc = self.flow.store.get(self.id)
        self.assertEqual(doc["generation_count"], 1)
        self.assertEqual(sum(a["step"] == "generation" for a in doc["attempts"]), 1)
        self.assertEqual(self.gateway.count_tasks(), 2)

    def test_same_key_different_content_conflicts(self):
        reviewed = self.approve_prompt()
        arguments = copy.deepcopy(reviewed["attempts"][0]["arguments"])
        arguments["prompt"] += "修改"
        self.assert_code("REQUEST_CONFLICT", lambda: self.gateway.submit_task(arguments))

    def test_unsupported_video_and_parameters_never_submit(self):
        self.approve_prompt()
        self.config.max_generations = 1
        bad = dict(self.spec, parameters=dict(self.spec["parameters"], unknown=123))
        self.id = self.flow.create(bad)["id"]
        self.approve_prompt()
        count = self.gateway.count_tasks()
        self.assert_code("UNSUPPORTED_PARAMETERS", lambda: self.flow.dispatch_generation(self.id))
        self.assertEqual(self.gateway.count_tasks(), count)
        video = dict(self.spec, media_type="video", parameters={"duration_seconds": 3})
        self.id = self.flow.create(video)["id"]
        self.approve_prompt()
        self.assert_code("CAPABILITY_UNVERIFIED", lambda: self.flow.dispatch_generation(self.id))

    def test_corrupt_source_stays_collecting(self):
        self.approve_prompt()
        generated = self.flow.dispatch_generation(self.id)
        self.flow.poll(self.id)
        task_id = generated["attempts"][-1]["hub_task_id"]
        artifact = self.gateway.get_task(task_id)["result"]["media_artifacts"][0]
        path = Path(self.gateway.get_artifact(artifact["artifact_id"])["transfer"]["path"])
        path.write_bytes(b"broken image")
        with self.assertRaises(FlowError):
            self.flow.collect(self.id)
        self.assertEqual(self.flow.store.get(self.id)["state"], "collecting")
        self.assertEqual(self.flow.store.get(self.id)["artifacts"], [])

    def test_delivery_detects_changed_file(self):
        doc = self.generate_and_collect()
        self.flow.quality_review(self.id, "approved", "人工测试", "human_image", "fake文件复核")
        Path(doc["artifacts"][0]["local_path"]).write_bytes(b"changed")
        self.assert_code("ARTIFACT_CHANGED", lambda: self.flow.deliver(self.id))
        self.assertEqual(self.flow.store.get(self.id)["state"], "ready_to_deliver")

    def test_bounded_rework_and_generations(self):
        self.config.max_revisions = 1
        self.config.max_generations = 1
        self.generate_and_collect()
        self.flow.quality_review(self.id, "rejected", "人工测试", "human_image", "需要返工")
        self.flow.revise(self.id, self.spec)
        self.approve_prompt()
        self.assert_code("GENERATION_LIMIT", lambda: self.flow.dispatch_generation(self.id))
        self.assert_code("REVISION_LIMIT", lambda: self.flow.revise(self.id, self.spec))

    def test_cancel_before_generation_and_uncertain_terminal(self):
        doc = self.flow.cancel(self.id, "测试取消草稿")
        self.assertEqual(doc["state"], "cancelled")
        self.assert_code("REVIEW_GATE_BLOCKED", lambda: self.flow.dispatch_generation(self.id))
        for state in ("interrupted", "delivery_unknown", "cancelled", "timed_out"):
            normalized = normalize_task({"state": state}, "task")
            self.assertTrue(normalized["execution_uncertain"])
        self.assertFalse(normalize_task({"state": "cancelled", "result": {"remote_cancelled": True}}, "task")["execution_uncertain"])

    def test_protocol_unknown_state_and_version_rejected(self):
        self.assert_code("UNKNOWN_STATE", lambda: normalize_task({"state": "maybe-done"}, "task"))
        self.assert_code("CONTRACT_VERSION", lambda: versioned({"contract_version": "2.0"}))
        self.assert_code("CONTRACT_VERSION", lambda: versioned({}))

    def test_contradictory_approval_rejected(self):
        request = self.flow._compile(self.flow.store.get(self.id)["versions"][0]["spec"])
        review = {"decision": "approved", "fingerprint": request["fingerprint"], "policy_version": "1.0",
                  "issues": [], "checks": [{"id": "x", "passed": False, "detail": "检查失败"}]}
        self.assert_code("INVALID_REVIEW", lambda: parse_review(review, request))


if __name__ == "__main__":
    unittest.main()

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from media_flow.config import Config
from media_flow.domain import validate_spec
from media_flow.errors import FlowError
from media_flow.gateways import FakeGateway
from media_flow.workflows import MediaFlow

ROOT = Path(__file__).resolve().parents[1]


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.gateway = FakeGateway(root / "hub")
        self.config = Config(root / "flow", gateway="fake", same_host=True,
                             trusted_artifact_roots=[str(self.gateway.artifact_root)])
        self.flow = MediaFlow(self.config, self.gateway)
        self.spec = json.loads((ROOT / "examples/anime-image.fake.json").read_text())
        self.ident = self.flow.create(self.spec)["id"]

    def approve(self):
        self.flow.request_review(self.ident)
        self.flow.poll(self.ident)

    def collect(self):
        self.approve()
        self.flow.dispatch_generation(self.ident)
        self.flow.poll(self.ident)
        return self.flow.collect(self.ident)

    def test_mode_change_requires_new_review(self):
        self.approve()
        self.config.generator_mode = "other-mode"
        with self.assertRaises(FlowError) as error:
            self.flow.dispatch_generation(self.ident)
        self.assertEqual(error.exception.code, "REVIEW_STALE")
        self.assertEqual(self.gateway.count_tasks(), 1)

    def test_artifact_metadata_cannot_change_between_result_and_download(self):
        self.approve()
        self.flow.dispatch_generation(self.ident)
        self.flow.poll(self.ident)
        original = self.gateway.get_artifact
        def changed(ident):
            value = original(ident)
            value["artifact"]["sha256"] = "0" * 64
            return value
        with patch.object(self.gateway, "get_artifact", side_effect=changed):
            with self.assertRaises(FlowError) as error:
                self.flow.collect(self.ident)
        self.assertEqual(error.exception.code, "INVALID_ARTIFACT")
        self.assertEqual(self.flow.store.get(self.ident)["state"], "collecting")

    def test_manifest_write_failure_does_not_mark_delivered(self):
        self.collect()
        self.flow.quality_review(self.ident, "approved", "测试", "human_image", "只确认模拟文件")
        with patch("media_flow.workflows.os.replace", side_effect=OSError("模拟磁盘故障")):
            with self.assertRaises(OSError):
                self.flow.deliver(self.ident)
        self.assertEqual(self.flow.store.get(self.ident)["state"], "ready_to_deliver")
        self.assertEqual(self.flow.deliver(self.ident)["state"], "delivered")

    def test_storyboard_rejects_unknown_character_and_wrong_duration(self):
        video = dict(self.spec, media_type="video", parameters={"duration_seconds": 4},
            characters=[{"id": "hero", "description": "蓝发成年角色"}],
            storyboard=[{"id": "s1", "duration_seconds": 4, "prompt": "街道", "movement": "慢速推镜",
                         "character_ids": ["hero"]}])
        self.assertEqual(validate_spec(video)["storyboard"][0]["duration_seconds"], 4)
        for field, value in (("character_ids", ["missing"]), ("duration_seconds", 3),
                             ("duration_seconds", float("nan"))):
            wrong = copy.deepcopy(video)
            wrong["storyboard"][0][field] = value
            with self.assertRaises(FlowError):
                validate_spec(wrong)

    def test_new_controller_continues_existing_generation_without_new_task(self):
        self.approve()
        generated = self.flow.dispatch_generation(self.ident)
        task_id = generated["attempts"][-1]["hub_task_id"]
        changed = replace(self.config, controller_agent="replacement-controller")
        resumed = MediaFlow(changed, FakeGateway(self.gateway.root))
        handoff = resumed.handoff(self.ident)
        self.assertEqual(handoff["workflow"]["attempts"][-1]["hub_task_id"], task_id)
        self.assertEqual(resumed.resume(self.ident)["state"], "collecting")
        resumed.collect(self.ident)
        resumed.quality_review(self.ident, "approved", "测试", "human_image", "仅确认模拟文件")
        self.assertEqual(resumed.deliver(self.ident)["state"], "delivered")
        self.assertEqual(resumed.gateway.count_tasks(), 2)
        self.assertEqual(resumed.store.events(self.ident)[-1]["actor"], "replacement-controller")

    def test_other_generator_reviewer_and_model_use_same_complete_workflow(self):
        # 仅替换通用能力和配置；模拟服务不代表这些执行器实际存在。
        capabilities = self.gateway.get_capabilities()
        reviewer = capabilities["agents"].pop("claude")
        generator = capabilities["agents"].pop("muse")
        reviewer["modes"] = ["review-mode"]
        generator["modes"] = ["generate-mode"]
        generator["capabilities"][0]["model"] = "alternate-test-model"
        capabilities["agents"] = {"alternate-reviewer": reviewer, "alternate-generator": generator}
        changed = replace(self.config, controller_agent="other-controller",
            prompt_reviewer_agent="alternate-reviewer", reviewer_mode="review-mode",
            generator_agent="alternate-generator", generator_mode="generate-mode",
            enabled_generators=["alternate-generator"])
        flow = MediaFlow(changed, self.gateway)
        ident = flow.create(dict(self.spec, model="alternate-test-model"))["id"]
        with patch.object(self.gateway, "get_capabilities", return_value=capabilities):
            flow.request_review(ident)
            flow.poll(ident)
            generated = flow.dispatch_generation(ident)
            flow.poll(ident)
            collected = flow.collect(ident)
            self.assertEqual(collected["state"], "quality_review_pending")
            flow.quality_review(ident, "approved", "测试", "human_image", "仅确认替换配置的模拟闭环")
            delivered = flow.deliver(ident)
        self.assertEqual(delivered["state"], "delivered")
        self.assertEqual(delivered["delivery_manifest"]["validation_mode"], "fake")
        self.assertEqual(generated["attempts"][0]["arguments"]["agent"], "alternate-reviewer")
        generation = generated["attempts"][-1]
        self.assertEqual(generation["arguments"]["agent"], "alternate-generator")
        self.assertEqual(generation["arguments"]["mode"], "generate-mode")
        self.assertEqual(generation["capability_trace"]["model"], "alternate-test-model")
        self.assertEqual(self.gateway.count_tasks(), 2)


if __name__ == "__main__":
    unittest.main()

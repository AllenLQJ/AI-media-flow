"""真实本地 MP4 文件检查；夹具和执行器均非 Muse，不证明模型视频能力。"""
import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from media_flow.artifacts import inspect_media, file_hash
from media_flow.config import Config
from media_flow.domain import fingerprint
from media_flow.errors import FlowError
from media_flow.gateways import FakeGateway, normalize_task
from media_flow.workflows import MediaFlow

FFMPEG = os.environ.get("MEDIAFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("MEDIAFLOW_TEST_FFPROBE") or shutil.which("ffprobe")


class VideoFixtureGateway(FakeGateway):
    """仅用于隔离测试，以确定的本地 MP4 替代模拟图片输出。"""
    def __init__(self, root, fixture):
        super().__init__(root)
        self.video = self.artifact_root / "local-fixture.mp4"
        shutil.copyfile(fixture, self.video)
        self.video_tasks = {}

    def get_capabilities(self):
        value = super().get_capabilities()
        value["agents"]["muse"]["capabilities"] = [{
            "kind": "video.generate", "model": "local-mp4-test", "verification": "verified",
            "evidence": "本地测试夹具，不是真实生成能力", "input_transports": [],
            "output_mime_types": ["video/mp4"],
            "parameters": {"count": {"const": 1}, "format": {"const": "mp4"},
                "width": {"const": 64}, "height": {"const": 48},
                "duration_seconds": {"type": "number", "minimum": .1, "maximum": 5},
                "fps": {"enum": [12, 24]}},
            "required_parameters": ["count", "format", "duration_seconds"]}]
        return value

    def submit_task(self, arguments):
        if arguments["context"]["correlation"]["step_id"] != "generation":
            return super().submit_task(arguments)
        key = arguments["request_id"]
        digest = fingerprint(arguments)
        if key in self.video_tasks:
            old_digest, task = self.video_tasks[key]
            if old_digest != digest:
                raise FlowError("REQUEST_CONFLICT", "测试夹具的幂等键内容不同")
            return copy.deepcopy(task)
        ident = "fake-video-" + digest[:24]
        artifact = {"artifact_id": ident + "-artifact", "task_id": ident, "filename": self.video.name,
            "mime_type": "video/mp4", "size_bytes": self.video.stat().st_size, "sha256": file_hash(self.video)}
        task = {"contract_version": "1.0", "id": ident, "state": "succeeded",
            "result": {"answer": "本地 MP4 测试夹具", "media_artifacts": [artifact], "fake": True}}
        self.video_tasks[key] = (digest, task)
        return copy.deepcopy(task)

    def get_task(self, task_id):
        for _, task in self.video_tasks.values():
            if task["id"] == task_id:
                return normalize_task(copy.deepcopy(task), task_id)
        return super().get_task(task_id)

    def get_artifact(self, artifact_id):
        for _, task in self.video_tasks.values():
            artifact = task["result"]["media_artifacts"][0]
            if artifact["artifact_id"] == artifact_id:
                return {"contract_version": "1.0", "artifact": copy.deepcopy(artifact), "transfer": {
                    "kind": "local_file", "host_id": "fake-local-host", "path": str(self.video),
                    "allowed_root": str(self.artifact_root)}}
        return super().get_artifact(artifact_id)


@unittest.skipUnless(FFMPEG and FFPROBE, "真实 MP4 测试需要配置 ffmpeg 和 ffprobe")
class VideoFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        cls.root = Path(cls.fixtures.name)
        cls.video = cls.root / "valid.mp4"
        cls.audio = cls.root / "audio-only.m4a"
        cls.padded = cls.root / "long-audio.mp4"
        cls._encode(["-f", "lavfi", "-i", "testsrc2=size=64x48:rate=12", "-t", "1",
            "-c:v", "mpeg4", "-q:v", "4", "-movflags", "+faststart", str(cls.video)])
        cls._encode(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "1",
            "-c:a", "aac", "-vn", str(cls.audio)])
        cls._encode(["-i", str(cls.video), "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "2", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", str(cls.padded)])

    @classmethod
    def _encode(cls, args):
        result = subprocess.run([FFMPEG, "-v", "error", "-y", *args], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("本地视频夹具编码失败：" + result.stderr)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def assert_code(self, code, operation):
        with self.assertRaises(FlowError) as error:
            operation()
        self.assertEqual(error.exception.code, code)

    def test_real_mp4_frames_dimensions_duration_and_frame_rate(self):
        value = inspect_media(self.video, "video", FFPROBE)
        self.assertEqual((value["width"], value["height"]), (64, 48))
        self.assertEqual(value["frame_count"], 12)
        self.assertEqual(value["fps"], 12)
        self.assertAlmostEqual(value["duration_seconds"], 1, places=2)
        self.assertEqual(value["mime_type"], "video/mp4")

    def test_video_duration_does_not_use_longer_audio_track(self):
        value = inspect_media(self.padded, "video", FFPROBE)
        self.assertEqual(value["frame_count"], 12)
        self.assertAlmostEqual(value["duration_seconds"], 1, places=2)

    def test_audio_only_and_corrupt_video_are_rejected(self):
        broken = self.directory / "broken.mp4"
        broken.write_bytes(self.video.read_bytes()[:20])
        for path in (self.audio, broken):
            with self.subTest(file=path.name):
                self.assert_code("INVALID_MEDIA", lambda: inspect_media(path, "video", FFPROBE))

    def test_readable_container_with_corrupt_payload_does_not_pass(self):
        # 保留 faststart 的 moov 元数据，只破坏 mdat：只读头的探测可能成功。
        data = bytearray(self.video.read_bytes())
        offset = 0
        while offset + 8 <= len(data):
            size = int.from_bytes(data[offset:offset + 4], "big")
            if size < 8 or offset + size > len(data):
                self.fail("测试夹具的 MP4 box 不完整")
            if data[offset + 4:offset + 8] == b"mdat":
                data[offset + 8:offset + size] = b"\x00" * (size - 8)
                break
            offset += size
        else:
            self.fail("测试夹具缺少 mdat")
        corrupt = self.directory / "corrupt-payload.mp4"
        corrupt.write_bytes(data)
        header = subprocess.run([FFPROBE, "-v", "error", "-show_format", "-of", "json", str(corrupt)],
                                capture_output=True, text=True, check=True, timeout=30)
        self.assertGreater(float(json.loads(header.stdout)["format"]["duration"]), 0)
        self.assert_code("INVALID_MEDIA", lambda: inspect_media(corrupt, "video", FFPROBE))

    def test_missing_configured_probe_is_explicit(self):
        self.assert_code("VIDEO_PROBE_UNAVAILABLE", lambda: inspect_media(self.video, "video",
            str(self.directory / "missing-ffprobe")))

    def _flow(self, duration=1, fps=12):
        gateway = VideoFixtureGateway(self.directory / "hub", self.video)
        config = Config(self.directory / "flow", gateway="fake", same_host=True,
            trusted_artifact_roots=[str(gateway.artifact_root)], ffprobe_command=FFPROBE)
        flow = MediaFlow(config, gateway)
        ident = flow.create({"goal": "本地视频文件流程测试", "media_type": "video", "model": "local-mp4-test",
            "prompt": "本地彩色测试图案，不是真实生成", "acceptance": ["仅验证文件与状态机"],
            "parameters": {"count": 1, "format": "mp4", "duration_seconds": duration,
                           "width": 64, "height": 48, "fps": fps}})["id"]
        flow.request_review(ident)
        flow.poll(ident)
        flow.dispatch_generation(ident)
        flow.poll(ident)
        return flow, ident

    def test_video_workflow_requires_full_review_and_records_fake_delivery(self):
        flow, ident = self._flow()
        collected = flow.collect(ident)
        self.assertEqual(collected["state"], "quality_review_pending")
        self.assert_code("REVIEW_COVERAGE_INSUFFICIENT", lambda: flow.quality_review(ident,
            "approved", "测试", "human_video_frames", "抽帧不能批准完整交付"))
        self.assert_code("DELIVERY_BLOCKED", lambda: flow.deliver(ident))
        flow.quality_review(ident, "approved", "测试", "human_video_full", "模拟人工确认，仅验证测试夹具流程")
        final = flow.deliver(ident)
        self.assertEqual(final["state"], "delivered")
        self.assertEqual(final["delivery_manifest"]["validation_mode"], "fake")
        artifact = final["delivery_manifest"]["artifacts"][0]
        self.assertEqual(artifact["sha256"], file_hash(self.video))
        self.assertEqual(artifact["metadata"]["frame_count"], 12)

    def test_wrong_duration_and_frame_rate_stay_collecting(self):
        for duration, fps, code in ((2, 12, "DURATION_MISMATCH"), (1, 24, "FRAME_RATE_MISMATCH")):
            with self.subTest(code=code):
                flow, ident = self._flow(duration, fps)
                self.assert_code(code, lambda: flow.collect(ident))
                doc = flow.store.get(ident)
                self.assertEqual(doc["state"], "collecting")
                self.assertEqual(doc["artifacts"], [])


if __name__ == "__main__":
    unittest.main()

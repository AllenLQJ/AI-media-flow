import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from media_flow.artifacts import ArtifactCollector, inspect_media
from media_flow.config import Config
from media_flow.errors import FlowError


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()
        self.source = self.allowed / "source.png"
        Image.new("RGB", (32, 32), "green").save(self.source)
        self.collector = ArtifactCollector(Config(self.root / "flow", gateway="fake", same_host=True,
                                                    trusted_artifact_roots=[str(self.allowed)]))
        self.value = {"contract_version": "1.0", "artifact": {"artifact_id": "a", "task_id": "task", "filename": "../ignored.png",
            "mime_type": "image/png", "size_bytes": self.source.stat().st_size,
            "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()},
            "transfer": {"kind": "local_file", "host_id": "host", "path": str(self.source), "allowed_root": str(self.allowed)}}

    def collect(self, value=None, host="host"):
        return self.collector.collect(value or self.value, "task", "image", host, self.root / "output")

    def assert_error(self, code, value=None, host="host"):
        with self.assertRaises(FlowError) as context:
            self.collect(value, host)
        self.assertEqual(context.exception.code, code)
        self.assertEqual(list((self.root / "output").glob(".collect-*")), [])

    def test_valid_png_and_untrusted_filename_ignored(self):
        result = self.collect()
        self.assertEqual(Path(result["local_path"]).parent, self.root / "output")
        self.assertEqual(result["metadata"]["width"], 32)

    def test_symlink_escape_denied(self):
        outside = self.root / "outside.png"
        outside.write_bytes(self.source.read_bytes())
        link = self.allowed / "link.png"
        link.symlink_to(outside)
        self.value["transfer"]["path"] = str(link)
        self.assert_error("ARTIFACT_PATH_DENIED")

    def test_host_root_task_and_mime_mismatch(self):
        self.assert_error("HOST_MISMATCH", host="other-host")
        wrong = copy.deepcopy(self.value)
        wrong["transfer"]["allowed_root"] = str(self.root)
        self.assert_error("ARTIFACT_PATH_DENIED", wrong)
        wrong = copy.deepcopy(self.value)
        wrong["artifact"]["task_id"] = "other-task"
        self.assert_error("INVALID_ARTIFACT", wrong)
        wrong = copy.deepcopy(self.value)
        wrong["artifact"]["mime_type"] = "image/jpeg"
        self.assert_error("MIME_MISMATCH", wrong)

    def test_hash_size_and_decode_failures(self):
        wrong = copy.deepcopy(self.value)
        wrong["artifact"]["sha256"] = "0" * 64
        self.assert_error("HASH_MISMATCH", wrong)
        wrong = copy.deepcopy(self.value)
        wrong["artifact"]["size_bytes"] += 1
        self.assert_error("SIZE_MISMATCH", wrong)
        self.source.write_bytes(b"this is not an image")
        wrong = copy.deepcopy(self.value)
        wrong["artifact"]["size_bytes"] = self.source.stat().st_size
        wrong["artifact"].pop("sha256")
        self.assert_error("INVALID_MEDIA", wrong)

    def test_missing_file_and_unknown_transfer(self):
        self.source.unlink()
        self.assert_error("ARTIFACT_NOT_FOUND")
        wrong = copy.deepcopy(self.value)
        wrong["transfer"]["kind"] = "base64"
        self.assert_error("UNSUPPORTED_TRANSFER", wrong)


if __name__ == "__main__":
    unittest.main()

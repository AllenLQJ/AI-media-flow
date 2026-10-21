import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import warnings
from pathlib import Path
from fractions import Fraction
from urllib.parse import urlsplit
from PIL import Image
from .errors import FlowError
from .gateways import versioned


def inspect_media(path, media_type, ffprobe_command="ffprobe"):
    if media_type == "image":
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    fmt, width, height = image.format, image.width, image.height
                    image.verify()
                with Image.open(path) as image:
                    image.load()
            if fmt not in {"PNG", "JPEG"}:
                raise ValueError("格式不是PNG/JPEG")
            return {"mime_type": "image/png" if fmt == "PNG" else "image/jpeg", "width": width, "height": height}
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise FlowError("INVALID_MEDIA", "图片签名或解码检查失败") from error
    executable = shutil.which(ffprobe_command)
    if not executable:
        raise FlowError("VIDEO_PROBE_UNAVAILABLE", "未安装 ffprobe，不能验收视频文件")
    try:
        # 扫描解码帧，不能仅凭仍可读取的容器头把损坏的视频验收通过。
        probe = subprocess.run([executable, "-v", "error", "-count_frames", "-show_format", "-show_streams",
                                "-of", "json", str(path)], capture_output=True, text=True, check=True, timeout=30)
        if probe.stderr.strip():
            raise ValueError("视频扫描出现解码错误")
        value = json.loads(probe.stdout)
        video = next(s for s in value["streams"] if s["codec_type"] == "video"
                     and not s.get("disposition", {}).get("attached_pic"))
        # 音轨可能比画面长；优先使用实际视频流时长而非整体容器时长。
        duration = float(video.get("duration", value["format"]["duration"]))
        if not math.isfinite(duration) or duration <= 0 or "mp4" not in value["format"]["format_name"].split(","):
            raise ValueError("无有效MP4视频")
        frame_count = int(video["nb_read_frames"])
        if int(video["width"]) <= 0 or int(video["height"]) <= 0 or frame_count <= 0:
            raise ValueError("视频尺寸或可解码帧数无效")
        rate = video.get("avg_frame_rate", "0/0")
        fps = float(Fraction(rate)) if rate != "0/0" else None
        if fps is not None and (not math.isfinite(fps) or fps <= 0):
            raise ValueError("帧率无效")
        return {"mime_type": "video/mp4", "width": int(video["width"]), "height": int(video["height"]),
                "duration_seconds": duration, "codec": video["codec_name"], "frame_count": frame_count, "fps": fps}
    except OSError as error:
        raise FlowError("VIDEO_PROBE_UNAVAILABLE", "配置的视频探测工具无法启动") from error
    except (ValueError, TypeError, KeyError, StopIteration, ZeroDivisionError, subprocess.SubprocessError) as error:
        raise FlowError("INVALID_MEDIA", "视频容器、流或时长检查失败") from error


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArtifactCollector:
    def __init__(self, config):
        self.config = config

    def collect(self, description, task_id, media_type, host_id, directory):
        value = versioned(description)
        artifact, transfer = value.get("artifact"), value.get("transfer")
        if not isinstance(artifact, dict) or not isinstance(transfer, dict):
            raise FlowError("INVALID_ARTIFACT", "附件缺少描述或传输方式")
        for name in ("artifact_id", "task_id", "filename", "mime_type"):
            if not isinstance(artifact.get(name), str) or not artifact[name]:
                raise FlowError("INVALID_ARTIFACT", "附件缺少必需字段")
        size = artifact.get("size_bytes")
        if type(size) is not int or not 0 < size <= self.config.max_file_bytes or artifact["task_id"] != task_id:
            raise FlowError("INVALID_ARTIFACT", "附件大小或来源任务不正确")
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".collect-", dir=root)
        temp = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as target:
                if transfer.get("kind") == "local_file":
                    if not self.config.same_host or transfer.get("host_id") != host_id:
                        raise FlowError("HOST_MISMATCH", "本机附件只能由同主机配置取回")
                    try:
                        source = Path(transfer["path"]).resolve(strict=True)
                        allowed = Path(transfer["allowed_root"]).resolve(strict=True)
                    except (KeyError, OSError) as error:
                        raise FlowError("ARTIFACT_NOT_FOUND", "附件文件或约定根目录不存在") from error
                    trusted = [Path(p).expanduser().resolve() for p in self.config.trusted_artifact_roots]
                    if not source.is_relative_to(allowed) or not any(allowed.is_relative_to(p) for p in trusted):
                        raise FlowError("ARTIFACT_PATH_DENIED", "附件路径超出客户端信任的产物目录")
                    with source.open("rb") as reader:
                        self._copy(iter(lambda: reader.read(1024 * 1024), b""), target, size)
                elif transfer.get("kind") == "https":
                    import httpx
                    url = transfer.get("url", "")
                    parsed = urlsplit(url)
                    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                        raise FlowError("INVALID_TRANSFER", "附件链接必须为无用户凭证的 HTTPS 地址")
                    with httpx.stream("GET", url, timeout=30, follow_redirects=False) as response:
                        if response.status_code != 200:
                            raise FlowError("DOWNLOAD_FAILED", "附件下载失败，HTTP状态：" + str(response.status_code))
                        self._copy(response.iter_bytes(1024 * 1024), target, size)
                else:
                    raise FlowError("UNSUPPORTED_TRANSFER", "未知附件传输方式")
                target.flush()
                os.fsync(target.fileno())
            if temp.stat().st_size != size:
                raise FlowError("SIZE_MISMATCH", "附件实际大小与声明不匹配")
            digest = file_hash(temp)
            if artifact.get("sha256") and artifact["sha256"] != digest:
                raise FlowError("HASH_MISMATCH", "附件哈希不匹配")
            metadata = inspect_media(temp, media_type, self.config.ffprobe_command)
            if metadata["mime_type"] != artifact["mime_type"]:
                raise FlowError("MIME_MISMATCH", "实际文件格式与声明MIME不一致")
            extension = {"image/png": ".png", "image/jpeg": ".jpg", "video/mp4": ".mp4"}[metadata["mime_type"]]
            final = root / (digest + extension)
            os.replace(temp, final)
            return dict(artifact, sha256=digest, local_path=str(final), metadata=metadata,
                        quality_review=None)
        except FlowError:
            raise
        except Exception as error:
            raise FlowError("COLLECTION_FAILED", "附件读取失败：" + type(error).__name__, retryable=True) from None
        finally:
            temp.unlink(missing_ok=True)

    def _copy(self, chunks, target, declared_size):
        total = 0
        for chunk in chunks:
            total += len(chunk)
            if total > min(declared_size, self.config.max_file_bytes):
                raise FlowError("FILE_TOO_LARGE", "附件数据超过声明大小或配置上限")
            target.write(chunk)

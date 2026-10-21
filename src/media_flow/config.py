import json
from dataclasses import dataclass, field
from pathlib import Path
from .errors import FlowError


@dataclass
class Config:
    data_dir: Path
    controller_agent: str = "codex"
    prompt_reviewer_agent: str = "claude"
    visual_reviewer_agent: str | None = None
    generator_agent: str = "muse"
    generator_mode: str = "delegate"
    reviewer_mode: str = "text_only"
    enabled_generators: list[str] = field(default_factory=lambda: ["muse"])
    policy_version: str = "1.0"
    compiler_version: str = "1.0"
    gateway: str = "mcp"
    hub_command: str = ""
    hub_args: list[str] = field(default_factory=list)
    hub_env: dict[str, str] = field(default_factory=dict)
    hub_cwd: str | None = None
    trusted_artifact_roots: list[str] = field(default_factory=list)
    same_host: bool = False
    max_generations: int = 3
    max_revisions: int = 3
    timeout_seconds: int = 600
    max_file_bytes: int = 100_000_000
    ffprobe_command: str = "ffprobe"
    lease_seconds: int = 30

    def __post_init__(self):
        self.data_dir = Path(self.data_dir).expanduser().resolve()
        if self.generator_agent not in self.enabled_generators:
            raise FlowError("GENERATOR_DISABLED", "生成 Agent 未在启用名单中")
        for name in ("max_generations", "max_revisions", "max_file_bytes", "lease_seconds"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise FlowError("INVALID_CONFIG", name + " 必须为正整数")
        if type(self.timeout_seconds) is not int or not 10 <= self.timeout_seconds <= 7200:
            raise FlowError("INVALID_CONFIG", "任务期限必须为10至7200秒")
        if self.gateway not in {"mcp", "fake"}:
            raise FlowError("INVALID_CONFIG", "未知 gateway")
        if type(self.same_host) is not bool:
            raise FlowError("INVALID_CONFIG", "same_host 必须为布尔值")
        for name in ("controller_agent", "prompt_reviewer_agent", "generator_agent", "generator_mode", "reviewer_mode",
                     "policy_version", "compiler_version", "ffprobe_command"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise FlowError("INVALID_CONFIG", name + " 必须为非空文本")
        if self.visual_reviewer_agent is not None:
            raise FlowError("CAPABILITY_UNIMPLEMENTED", "首版成品复核使用人工确认，自动视觉复核尚未接入")

    @classmethod
    def load(cls, path):
        source = Path(path).expanduser().resolve()
        raw = json.loads(source.read_text(encoding="utf-8"))
        directory = Path(raw.pop("data_dir", ".data")).expanduser()
        if not directory.is_absolute():
            directory = source.parent / directory
        return cls(data_dir=directory, **raw)

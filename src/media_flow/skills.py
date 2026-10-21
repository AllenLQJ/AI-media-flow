from pathlib import Path
from .domain import fingerprint
from .errors import FlowError


class SkillLibrary:
    def __init__(self, root=None):
        self.root = Path(root) if root else Path(__file__).resolve().parents[2] / "skills"

    def versions(self):
        versions = {}
        for folder in sorted(self.root.iterdir()):
            if folder.is_dir() and (folder / "SKILL.md").is_file():
                files = {str(p.relative_to(folder)): p.read_text(encoding="utf-8")
                         for p in sorted(folder.rglob("*")) if p.is_file() and not p.name.startswith(".")}
                versions[folder.name] = fingerprint(files)
        if len(versions) != 5:
            raise FlowError("SKILL_LIBRARY_INCOMPLETE", "项目需要五个完整的媒体 Skill")
        return versions

    def read(self, relative):
        return (self.root / relative).read_text(encoding="utf-8")

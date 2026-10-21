import argparse
import json
import sys
from pathlib import Path
from .config import Config
from .errors import FlowError, redact
from .gateways import FakeGateway, MCPGateway
from .workflows import MediaFlow


def build_flow(config):
    if config.gateway == "fake":
        gateway = FakeGateway(config.data_dir / ".fake-hub")
        config.same_host = True
        config.trusted_artifact_roots = [str(gateway.artifact_root)]
    else:
        gateway = MCPGateway(config)
    return MediaFlow(config, gateway)


def main(argv=None):
    parser = argparse.ArgumentParser(description="MediaFlow 审核、生成和交付工作流（JSON输出）")
    parser.add_argument("--config", default="config.local.json", help="独立配置文件")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities", help="查询实际能力，不发送生成任务")
    sub.add_parser("skills", help="查看项目Skill内容版本")
    create = sub.add_parser("create", help="从JSON需求创建草稿")
    create.add_argument("--spec", required=True)
    for command in ("show", "events", "review", "generate", "poll", "collect", "resume", "handoff", "deliver"):
        sub.add_parser(command).add_argument("workflow_id")
    revise = sub.add_parser("revise")
    revise.add_argument("workflow_id")
    revise.add_argument("--spec", required=True)
    quality = sub.add_parser("quality-review", help="记录实际人工成品复核")
    quality.add_argument("workflow_id")
    quality.add_argument("--decision", choices=["approved", "rejected"], required=True)
    quality.add_argument("--actor", required=True)
    quality.add_argument("--coverage", choices=["human_image", "human_video_full", "human_video_frames"], required=True)
    quality.add_argument("--notes", required=True)
    cancel = sub.add_parser("cancel")
    cancel.add_argument("workflow_id")
    cancel.add_argument("--reason", required=True)
    human = sub.add_parser("human-resume", help="用户明确确认人工验证/登录/审批已处理后恢复原任务")
    human.add_argument("workflow_id")
    human.add_argument("--confirmed-by", required=True)
    human.add_argument("--notes", required=True)
    args = parser.parse_args(argv)
    try:
        flow = build_flow(Config.load(args.config))
        command = args.command
        if command == "capabilities":
            result = flow.gateway.get_capabilities()
        elif command == "skills":
            result = {"skill_versions": flow.skills.versions()}
        elif command == "create":
            result = flow.create(json.loads(Path(args.spec).read_text(encoding="utf-8")))
        elif command == "revise":
            result = flow.revise(args.workflow_id, json.loads(Path(args.spec).read_text(encoding="utf-8")))
        elif command == "show":
            result = flow.store.get(args.workflow_id)
        elif command == "events":
            result = flow.store.events(args.workflow_id)
        elif command == "review":
            result = flow.request_review(args.workflow_id)
        elif command == "generate":
            result = flow.dispatch_generation(args.workflow_id)
        elif command == "quality-review":
            result = flow.quality_review(args.workflow_id, args.decision, args.actor, args.coverage, args.notes)
        elif command == "cancel":
            result = flow.cancel(args.workflow_id, args.reason)
        elif command == "human-resume":
            result = flow.human_resume(args.workflow_id, args.confirmed_by, args.notes)
        else:
            result = getattr(flow, command)(args.workflow_id)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
        return 0
    except FlowError as error:
        print(json.dumps({"ok": False, "error": error.as_dict()}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"ok": False, "error": {"code": "INVALID_INPUT", "message": redact(error)}}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())

# MediaFlow

经审核的媒体工作流，通过独立 Agent Hub 的 stdio MCP 调用审核与生成 Agent。首版提供 JSON CLI、本地持久化、项目 Skills、审核门禁和成品文件收集。

当前完成本地开发和模拟闭环，运行 Hub 已更新到 0.2.1。MediaFlow 已完成两版真实文本审核、图片生成与独立文件取回，首版被用户拒绝，第二版写实国风样图经用户明确确认“符合，可以交付”，已记录 human_image/approved 并正式交付；详见 [Hub 0.2.1 验收记录](docs/2026-10-11-Hub-0.2.1-验收.md)。模拟图片始终带 `FAKE / FLOW TEST` 标记，不能替代真实证据。完整目标逐项证据和待完成事项见 [完成条件核对](docs/完成条件核对.md)。

当前只验证了 `text.review/deepseek-flash[1m]` 和 `image.generate/muse:auto`。`muse:auto` 是 Muse 默认图片路由，底层模型未知；支持一张 PNG、无参考图，尚未验证指定宽高。视频、参考图/编辑、跨图一致性尚未验收。

## 安装与测试

需要 Python 3.12 或更高。当前使用源码可编辑安装，保留整个项目目录（其中包含 Skills）。

2026-10-21 上传前核对：当前文件集及测试与部分历史记录不同，复现以本节和[上传前检查](docs/2026-10-21-GitHub上传检查.md)为准。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/fake_demo.py
```

也可 `python -m venv .venv` 后使用该环境 `python -m pip install -e .`。Unix 环境 Python 路径是 `.venv/bin/python`；Windows 是 `.venv\Scripts\python.exe`。本轮实际验证平台为 macOS Apple Silicon，Windows 未实际运行。

`fake_demo.py` 每步启动新的 CLI 进程，执行创建、审核、生成、收集、模拟复核与交付。输出工作流 ID、状态、512×512 PNG 路径、SHA-256 和清单路径；数据库和素材位于 `.data/fake-demo`，不进 Git。重复运行会创建新的模拟工作流。

七项本地 MP4 测试需要可执行的 ffmpeg/ffprobe；可从 PATH 查找，或分别设置 `MEDIAFLOW_TEST_FFMPEG` 和 `MEDIAFLOW_TEST_FFPROBE` 为绝对路径。工具缺失时这些测试显式跳过。2026-10-21 实际运行 39 项，32 项通过、7 项因缺少工具跳过；本地夹具不证明 Muse 视频生成能力。

## 连接 Hub

复制 `config.example.json` 为 `config.local.json`，填写 Hub 虚拟环境 Python 的绝对路径和经双方确认的产物根目录。通过 `hub_cwd` 指定 Hub 源码目录也可；`hub_env` 可提供必要环境，但不能提交真实凭证。

```sh
uv run media-flow --config config.local.json capabilities
uv run media-flow --config config.local.json skills
```

能力查询只读，不发送模型任务。必须同时具备已验证的审核 Agent `text.review` 和 Muse 对应模型 `image.generate`。`available=true` 只表示运行器就绪。Hub 提供实际模型或经约定的路由标识、已验证参数范围、证据、允许产物目录与服务版本；底层模型未知时明确标注。

已验收默认路由的真实需求示例是 `examples/anime-image.muse-auto.json`，参数只有 count=1、format=png；不要沿用 fake 示例的 width/height。`config.local.json` 已填写本机连接及授权产物根目录；首轮生成上限为一次，后续因用户要求调整改为累计两次，配置不进 Git。

用户后续提供的写实国风撑伞图片已整理为 `examples/cinematic-wuxia.muse-auto.json`。首张赛璐璐样图被用户拒绝，原工作流进入第二版重新审核；本机配置总生成上限为2，只允许本轮额外一张。原图只供总控观察，没有发送给 Muse，不能把这次文字生成当成参考图输入已支持。

Hub 服务端要求见 [实施约定](docs/HubGateway-v1-服务端实施约定.md)，代码中的 schema 在 `src/media_flow/schemas.py`。当前文件集不包含独立 `contracts/` 和契约导出脚本。文件由用户自行交给 Hub 项目的聊天；本项目没有代发消息。

## 图片操作示例

可立即用 `examples/fake.config.json` 和 `examples/anime-image.fake.json` 运行以下操作。真实运行时复制需求文件，将 `model` 改为 Hub 已验证模型，参数改为实际支持值，并改用 `config.local.json`。

```sh
uv run media-flow --config examples/fake.config.json create --spec examples/anime-image.fake.json
uv run media-flow --config examples/fake.config.json review <工作流ID>
uv run media-flow --config examples/fake.config.json resume <工作流ID>
uv run media-flow --config examples/fake.config.json generate <工作流ID>
uv run media-flow --config examples/fake.config.json resume <工作流ID>
uv run media-flow --config examples/fake.config.json collect <工作流ID>
```

尖括号是需替换的工作流 ID。真实任务可能异步运行；查看 `show`，在 `review_pending`、`generating` 或 `collecting` 状态时按需继续 `resume`。不要循环创建新任务来等待原任务。

拿到 `quality_review_pending` 后实际查看 `artifacts[].local_path`，按需求的验收条件检查图片。确认后才能记录人工复核并交付：

```sh
uv run media-flow --config config.local.json quality-review <工作流ID> --decision approved --actor 用户 --coverage human_image --notes "实际查看后的具体结论"
uv run media-flow --config config.local.json deliver <工作流ID>
```

质量不通过时使用 `rejected` 和具体问题，修订需求后重新审核：

```sh
uv run media-flow --config config.local.json revise <工作流ID> --spec 修改后的需求.json
uv run media-flow --config config.local.json review <工作流ID>
```

没有跳过审核参数。编辑素材、模型、生成 Agent、执行模式、Skill、编译器或政策后旧审核失效。默认最多生成 3 次、返工 3 次，按整个工作流累计。首轮真实二次元样张只做一次生成，后续根据用户反馈迭代。

## 查看、恢复与换总控

```sh
uv run media-flow --config config.local.json show <工作流ID>
uv run media-flow --config config.local.json events <工作流ID>
uv run media-flow --config config.local.json handoff <工作流ID>
uv run media-flow --config config.local.json resume <工作流ID>
uv run media-flow --config config.local.json cancel <工作流ID> --reason "用户停止"
```

新总控使用相同数据目录即可读取需求版本、审核、原 Hub task ID、素材和下一动作。`controller_agent` 是审计操作者标识；更换它不改变已审核的生成内容。生成 Agent 使用 `generator_agent`、`generator_mode`、`enabled_generators` 配置，并由 Hub 提供已验证能力。模型写在需求文件中。

响应丢失后 `resume` 重放同一完整请求及幂等键，依赖 Hub 的持久化幂等；不创建新的生成 attempt。取消请求与远端停止分开。不确定终态、取消后缺少任务 ID 等情况保持停止，需要核对原 Hub 任务；首版没有强制解除不确定状态的命令。

Hub 0.2.1 的 `waiting_for_user` 保持原任务运行状态，CLI 输出 needs_user=true 和 intervention，并停止自动推进。普通 `resume` 只查原任务，不代替用户确认。用户在 Muse 原会话处理完验证、登录或审批并明确确认后，再执行：

```sh
uv run media-flow --config config.local.json human-resume <工作流ID> --confirmed-by 用户 --notes "实际已处理的事项"
```

人工确认先持久化，调用 Hub `resume_task` 恢复原 task ID。同一介入恢复请求使用稳定幂等键，响应丢失不创建新生成任务。没有人工完成确认时不要执行该命令。

## 当前限制

- 成品复核使用人工确认；自动视觉审核尚未接入。配置非空 `visual_reviewer_agent` 会明确报错。
- PNG/JPEG 验证签名、解码、大小、哈希和请求尺寸；MP4 需要 `ffprobe`，可用配置 `ffprobe_command` 指定可执行名称或绝对路径。视频检查扫描实际帧，核对容器、视频流时长、尺寸及请求帧率，不把可读取文件头当成完整文件有效。Muse 视频生成仍未验收。
- 视频数据支持角色、分镜、时长与运动说明；无已验证视频能力时不提交生成。多镜头批量合成、自动抽帧尚未实现。
- 附件支持同主机可信路径及 HTTPS 流式下载；不跟随重定向，不持久化签名下载 URL。本机参考素材需要同主机 Hub；远程输入上传尚未实现。
- 本地文件保存与数据库并非跨资源事务；清单原子写入失败会回滚交付状态，崩溃后可重跑交付。跨设备状态迁移、路径重映射和 UI 留待后续。
- 代码和 Skills 可以 Git 同步，凭证、数据库、日志、生成文件和会话不能用 Git 同步。

开发决策见 [开发计划](docs/开发计划.md)、[架构与扩展](docs/架构与扩展.md)和[测试记录](docs/测试与集成记录.md)。历史记录提及的参考来源文档当前不在文件集中。

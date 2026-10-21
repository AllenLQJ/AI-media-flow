# 提供给 Agent Hub 聊天的服务端实施约定

日期：2026-10-11。状态：0.2.1 图片适配已实现，MediaFlow 两版真实审核、生成和文件取回已通过，第二版经用户质量确认后正式交付，图片闭环完成；视频和输入素材待完成。本文件由 MediaFlow 编写，服务端源码归 Hub。

## 最新验收进展（0.2.1）

运行服务当前返回 0.2.1，具有 verified text.review/deepseek-flash[1m] 和 image.generate/muse:auto。默认图片路由底层模型未知，只验证一张 PNG 无参考图，不支持指定宽高声明。Hub 已实现结构化图片附件识别、授权原图取回和登记；MediaFlow 已独立取回先前真实附件并核对解码、大小和哈希。本轮已完成新的二次元请求文本审核，图片业务验收详见 `2026-10-11-Hub-0.2.1-验收.md`。

同期新增 waiting_for_user 与 resume_task。MediaFlow 已兼容人工等待，不将其误报失败；需要用户实际处理后明确确认才恢复原任务。视频、输入附件和参考图/编辑仍不登记已验证能力，具体剩余条件见 [视频接入验收约定](视频接入验收约定.md)。下面 0.2.0 的缺口记录保留为历史，不代表 0.2.1 的当前图片实现。

## 历史实现核对（0.2.0）

实际服务 `get_capabilities` 返回 contract_version=1.0、service_version=0.2.0；`get_artifact` 对不存在的 ID 返回 ARTIFACT_NOT_FOUND。Hub 的交接文件 `../AI-Integration-platform/docs/HubGateway-v1-实现交接.md` 已说明通用能力查询、context 幂等、授权附件登记与 PNG/JPEG 取回实现，记录 136 项回归（135 通过、1 项 Windows ACL 跳过）。该回归结论来自 Hub 项目记录，本聊天没有重新运行其全套测试。

剩余工作不是仅填能力声明：当前 Muse 适配器仍只回收回答和 source_urls，尚无真实生成附件下载。Hub 的输入附件适配器、视频登记也尚未实现；所有运行 Agent 的 capabilities 当前均为空。不能把通用接口完成当成图片生成链路完成。

请 Hub 项目按以下顺序继续，MediaFlow 不修改服务端：

1. 独立验证文本审核执行器的实际模型能返回所需完整审核 JSON，登记 text.review 的任务 ID、日期和覆盖范围证据。
2. 在 Muse 适配器中识别明确的生成成品及其完成状态，通过实际支持的授权取回机制下载到 Hub 自己的 media-artifacts 目录，返回 media_artifacts。普通来源网址不能据扩展名推断为成品。不得由 MediaFlow 去读取 Muse 内部状态或代替 Hub 下载未经确认的来源链接。
3. 用一次有界真实 PNG/JPEG 生成验证模型标识、请求参数、生成状态、文件取回、解码及 SHA-256；只有通过后登记 image.generate 的 verified profile。提供产物根目录供客户端配置，不传凭证或临时签名链接到聊天。
4. 首轮真实 MediaFlow 图片不带参考素材；参考图/编辑后续需另外实现输入附件传输并验收，不能靠配置声明 input_transports 开启。
5. 图片链路通过后才做视频适配、真实 MP4 登记和能力验证。当前 Hub 拒绝 video/mp4 登记，不能先标 video.generate 已完成。

以上是既有真实图片/视频验收范围的具体缺口，用户自行在 Hub 聊天安排，不由本聊天发送消息。下面保留双方约定，已实现的通用接口无需另建第二套。

## 实现范围

复用现有 submit_task、get_task、get_result、cancel_task 和事件接口。新增通用能力查询 get_capabilities 和附件取回 get_artifact；扩展 submit_task 可选 context。现有调用不传 context 时保持兼容。

Hub 不解析 MediaSpec、不判断媒体质量、不管理媒体审核门禁。MediaFlow 不读取 Hub 内部数据库或运行文件。

## 1. get_capabilities

只读，返回 JSON 对象：

```json
{
  "contract_version": "1.0",
  "service_version": "可追踪的实现版本",
  "host_id": "本机稳定标识",
  "agents": {
    "muse": {
      "available": true,
      "modes": ["delegate"],
      "capabilities": [
        {
          "kind": "image.generate",
          "model": "实际模型标识",
          "verification": "unverified",
          "evidence": null,
          "parameters": {
            "format": {"enum": ["png", "jpeg"]},
            "count": {"type": "integer", "minimum": 1, "maximum": 1}
          },
          "input_transports": [],
          "output_mime_types": ["image/png", "image/jpeg"]
        }
      ]
    }
  }
}
```

- verification 枚举 verified、unverified、unsupported。已验证必须附 evidence（任务 ID/日期/结果覆盖范围），不可由可执行文件存在推断。
- kind 可取 text.review、image.generate、image.edit、video.generate、image.review、video.frames.review、video.full.review。
- Claude/DeepSeek 等记录实际 model，运行器名称不能证明视觉能力。
- 参数使用 JSON Schema 字段约束；缺失参数不视为任意支持。模型、宽高、比例、数量、时长、分辨率、帧率分别声明。
- 所需能力必须 verification=verified 才能在正常业务生成路径启用；未验证能力探针由 Hub 的独立验收记录，不绕过 MediaFlow 门禁。
- 首版允许旧 list_agents 诊断显示，但不能据此开放未知媒体能力。

## 2. submit_task 的兼容扩展

保留现有 request_id、agent、prompt、cwd、mode、timeout_seconds，增加可选 context 对象：

```json
{
  "contract_version": "1.0",
  "correlation": {
    "client_project": "media-flow",
    "workflow_id": "wf-ID",
    "step_id": "generation",
    "spec_revision": 1,
    "attempt": 1
  },
  "payload": {
    "attachments": [],
    "input_host_id": null,
    "requested_output": {"media_type": "image", "model": "实际模型标识"}
  }
}
```

- prompt 是已冻结的完整 instruction；不得另外改写。Hub 的跟踪标记只用于协议回收，不能改变业务语义。
- context 纳入持久化和请求内容哈希；同 request_id、相同完整参数返回同一任务，不同内容返回 REQUEST_CONFLICT。
- 附件引用由已验证 input_transports 决定，不能将 MediaFlow 本地路径假定成 Muse 可访问地址。
- 输入附件若支持 local_file，必须验证同主机 host_id、允许输入根目录、文件大小及 SHA-256；传给 Muse 的引用由适配器生成。
- `input_host_id` 在无参考素材时为 null；有本机素材时等于能力接口 host_id。首版 MediaFlow 不向远程 Hub 发送本机参考路径。
- 返回加 contract_version="1.0"；任务结果原样传输审核 JSON，审核判断由 MediaFlow 完成。
- 不确定发送保留 DELIVERY_UNKNOWN，不自动重发。底层执行重试和读取重试明确区分。

## 3. 任务状态与结果

get_task 保留原始 state；get_result 返回 ready、state、answer、errors、artifacts 等。

附件集合统一使用 `media_artifacts` 数组，保留既有 `artifacts.logs`：

```json
{
  "contract_version": "1.0",
  "ready": true,
  "state": "succeeded",
  "answer": "原始回答",
  "media_artifacts": [
    {
      "artifact_id": "artifact-ID",
      "task_id": "Hub任务ID",
      "filename": "result.png",
      "mime_type": "image/png",
      "size_bytes": 12345,
      "sha256": "64位小写哈希"
    }
  ]
}
```

- 普通 source_urls 不转成成品附件。产物身份由连接器真实附件信息或明确生成结果证明。
- 最终回复只证明回复完成；实际生成未完成时不得提供虚假的产物。
- gateway 映射 queued/running/succeeded/failed；cancelling 仍保持运行并另记取消请求；timed_out 映射失败+TASK_TIMED_OUT。
- interrupted/delivery_unknown 映射失败并置 execution_uncertain=true；MediaFlow 停止自动新 attempt，保留原始状态供查证。
- cancelled 需要区分本地等待停止与 remote_cancelled；未确认远端停止同样禁止盲目再次生成。
- 未知状态返回协议错误。Gateway 对外始终输出 contract_version="1.0"。

## 4. get_artifact(artifact_id)

只读地返回已授权的取回描述。建议首版本机文件：

```json
{
  "contract_version": "1.0",
  "artifact": {
    "artifact_id": "artifact-ID",
    "task_id": "Hub任务ID",
    "filename": "result.png",
    "mime_type": "image/png",
    "size_bytes": 12345,
    "sha256": "64位小写哈希"
  },
  "transfer": {
    "kind": "local_file",
    "host_id": "同get_capabilities",
    "path": "Hub约定产物目录里的绝对路径",
    "allowed_root": "Hub约定产物根目录"
  }
}
```

- artifact ID 持久且绑定 task ID。禁止任意路径读文件、越界、符号链接逃逸及已删除文件冒充存在。
- local_file 仅同主机，Hub 根目录由服务端配置，客户端配置另有可信根目录限制。
- 远程可返回 kind=https、url、expires_at；URL 不进入聊天、事件、交接或错误日志。传输描述只用于即时取回。
- 不返回大型 Base64。MediaFlow 临时写入、验证文件签名/解码/大小/哈希后原子归档。
- 附件缺失/过期返回 ARTIFACT_NOT_FOUND/ARTIFACT_EXPIRED；实际内容格式错误不能标完成。

## 5. 错误与兼容

错误 `{ "error": { "code": "...", "message": "...", "retryable": false, "details": {} } }`。
保留原有错误码，新接口错误补 retryable。message/details 必须脱敏；允许有界重试读取，不自动重试变更型操作。

## 6. 服务端验收清单

1. 旧八个 MCP 工具的已有测试通过；旧 submit_task 不传 context 仍正常。
2. context 幂等哈希覆盖完整内容；响应丢失重提同键返回原任务。
3. get_capabilities 不发送模型任务，不把 unverified 标为 verified。
4. 实际生成附件登记 task ID，稳定 artifact ID，提供 authorized transfer。
5. 来源网页链接不能冒充媒体附件；缺失文件、根目录越界及符号链接逃逸报错。
6. 取消请求与远端取消证据分离；不确定状态禁止自动重发。
7. 真实图片能力按生成、参考素材、编辑、取回分别登记证据；视频另验收。
8. 交接实际实现版本、工具 schema、测试命令和结果。不要在有在途任务时自行重启共享后台。

MediaFlow 双端契约测试会用 stdio MCP 检查上述返回格式；fake 测试不是 Muse 集成证据。

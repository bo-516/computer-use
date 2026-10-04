# Grok Computer

[English](README.md) | **简体中文**

为 Grok Build 提供 computer use（电脑操控）能力：一个 Grok Build 插件（`computer-use`）加一个自研的门面 MCP 服务（`grok-computer-mcp`），让 Grok Build 能在 macOS、Windows、Linux 的桌面应用和浏览器里看屏幕、点鼠标、敲键盘。主要用途是开发闭环里的界面验证（改代码 → 启动应用 → 看界面 → 再改），以及操作没有 API 的桌面和网页工具。

设计文档：[docs/goal.md](docs/goal.md)。安装与配置：[docs/install.md](docs/install.md)。

## 工作方式

```
主 agent ──委派──▶ computer 子 agent（独立上下文，只收发报告）
                     │
                     ├─▶ computer：grok-computer-mcp 门面 ──▶ Cua Driver ──▶ 桌面（宿主机 / 沙箱）
                     └─▶ browser：Playwright MCP（--isolated）
hooks：guard（风险分级与确认）· audit（审计）· verify_gate（报告与收尾验证）· selfcheck
```

- **截图不进主会话**：GUI 工作全部交给 `computer-use:computer` 子 agent，主 agent 只收到 ≤ 2 KB 的结构化报告（`STATUS / SUMMARY / EVIDENCE / BLOCKERS / NEXT`）。
- **无障碍树优先、像素最后**：观测默认是精简的元素列表（每个交互元素一行，带稳定 ref），树不够用时退到带编号的 Set-of-Mark 截图，再不行才用 grounding 模型或坐标。
- **一个坐标系**：模型看到和发送的坐标都是当前观测截图的像素坐标（长边 1280 px），HiDPI、窗口偏移、多显示器全部在门面里换算；界面变了，旧观测上的动作会被拒绝（`STALE_OBSERVATION`）。
- **两道安全闸门**：hooks 负责交互式确认（发送、删除、支付等高风险动作先问你），门面内置硬规则并在策略无法加载时拒绝一切（黑名单应用、安全输入框、危险快捷键、会话锁、急停）。

门面提供 9 个工具：`observe`、`click`、`type_text`、`press_keys`、`scroll`、`drag`、`apps`、`wait_for`、`locate`。

## 快速开始

```bash
curl -fsSL https://cua.ai/driver/install.sh | bash      # 桌面驱动（macOS 还需授权，见安装文档）
curl -LsSf https://astral.sh/uv/install.sh | sh         # 运行门面
grok plugin marketplace add bo-516/computer-use
grok plugin install computer-use --trust
grok plugin enable computer-use
uvx grok-computer-mcp@0.2.0 doctor                      # 自检
```

然后在 grok 里直接描述任务，或用 `/computer 打开设置，把深色模式打开并确认生效`。

**隐私**：截图会发送给模型服务商。黑名单应用（密码管理器、钥匙串、终端、银行等）不会被观测；轨迹只保存在本机，默认 7 天后清理。详见安装文档第 6 节。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `.grok-plugin/` | marketplace 索引与组件目录 |
| `plugins/computer-use/` | 插件：子 agent、skill 与平台说明、`/computer` 命令、hooks、`.mcp.json` |
| `servers/grok-computer-mcp/` | 门面 MCP 服务（Python 3.11+，PyPI 发布，`uvx` 运行） |
| `eval/` | 任务集（60 个 Cua Bench 变体）、宿主机运行器与指标、grounding 基准、Phase 0 探针，见 [eval/README.md](eval/README.md) |
| `docs/` | 设计文档、安装文档、Phase 0 验证记录、状态文件 schema |
| `scripts/` | hooklib 同步、plugin-index 生成 |
| `tests/` | hooks fixture 测试与仓库级契约测试 |

## 开发

需要 `uv`（Python 3.11 会自动安装）；开发闭环任务的测试需要 Node.js。

```bash
uv sync
uv run pytest                 # 全部测试（假后端，不需要桌面或账号）
uv run ruff check
uv run pyright
uv run --python 3.8 --no-project --with pytest==8.3.5 pytest tests/hooks   # hooks 在 Python 3.8 上
uv run python scripts/sync_hooklib.py --check     # 门面中的 hooklib 副本与插件一致
uv run python scripts/plugin_index.py --check     # 组件目录未过期
grok plugin validate plugins/computer-use
```

代码约定见 [AGENTS.md](AGENTS.md)。插件目录只放文件、hooks 只用标准库；门面是唯一调用 Cua Driver 的代码；安全相关规则（fail-closed、R3 不可放宽、文本脱敏等）不得回退。

## 状态

插件、门面、任务集、grounding 基准与 Phase 0 探针工具均已实现并通过测试（假后端与假 Cua Driver）。尚需在真实环境完成的工作——运行 Phase 0 探针与 grounding 基准、macOS/Windows/Linux 实机跑任务集——列在 [docs/goal.md 10.1](docs/goal.md) 与 [docs/phase0-verification.md](docs/phase0-verification.md)。

## 许可证

[Apache-2.0](LICENSE)。

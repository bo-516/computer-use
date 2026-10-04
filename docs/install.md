# 安装与配置

本文面向使用者：从零安装 computer-use 插件、完成自检、按需配置，并说明隐私与故障排查。设计细节见 [goal.md](goal.md)。

目标是 10 分钟内完成安装与自检（goal.md G4）。

## 1. 前置条件

| 依赖 | 用途 | 检查命令 |
|---|---|---|
| Grok Build（`grok`）1.0.46 或更新 | 宿主 | `grok --version` |
| `python3` ≥ 3.8 | 插件 hooks（只用标准库） | `python3 --version` |
| `uv` | 运行门面服务 `grok-computer-mcp`（经 `uvx`） | `uv --version` |
| Node.js ≥ 18 | 浏览器服务 Playwright MCP（经 `npx`） | `node --version` |
| Cua Driver | 桌面输入与截图（门面通过它操作桌面） | `cua-driver --help` |

插件只分发文件，不安装任何运行时，所以以上依赖需要自行安装。

## 2. 安装

### 2.1 Cua Driver

```bash
curl -fsSL https://cua.ai/driver/install.sh | bash
```

Windows 使用 PowerShell 安装脚本（见 Cua Driver 文档）。需要固定版本时，在运行安装脚本前设置 `CUA_DRIVER_RS_VERSION=<发布标签>`。

**macOS 权限**：在"系统设置 → 隐私与安全性"中给 **CuaDriver.app** 授予"辅助功能"和"屏幕录制"，然后以独立 daemon 运行，让权限归属于 CuaDriver.app 而不是终端：

```bash
open -n -g -a CuaDriver --args serve
```

门面默认通过 `cua-driver mcp` 连接这个 daemon，所以授权只需做一次，升级门面也不用重新授权。

### 2.2 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2.3 插件

```bash
grok plugin marketplace add bo-516/computer-use
grok plugin install computer-use --trust
grok plugin enable computer-use
```

企业环境要求固定提交（`require_sha`）时，直接安装某个提交：

```bash
grok plugin install bo-516/computer-use@<40 位 sha>#plugins/computer-use --trust
```

插件在被信任（`--trust`）之前不会加载 hooks 和 MCP 服务。

## 3. 自检

```bash
grok inspect
uvx grok-computer-mcp@0.2.0 doctor
```

- `grok inspect`：确认 `computer-use` 插件已启用，agents、hooks、MCP 服务（`computer`、`browser`）都已加载，且没有被组织策略拦截。
- `doctor`：检查 hooks 与门面的 Python 版本、`cua-driver`/`npx`/`uvx` 是否在 PATH 中、策略文件、状态与轨迹目录是否可写、桌面锁目录，然后连接 Cua Driver 检查系统权限，并确认规范截图在宿主重编码阈值以内（长边 1280 px、JPEG q80、≤ 400 KB）。关键项失败时给出修复提示，退出码非零。

每次会话开始时，插件的 SessionStart hook 也会做一次轻量自检；有问题时宿主会显示该 hook 失败，并附一行原因。

最后试一次：

```text
/computer 打开"计算器"，算 12 × 34，告诉我结果
```

## 4. 使用方式

- 直接用自然语言描述需要看界面或操作界面的任务，主 agent 会按 skill 的规范委派给 `computer-use:computer` 子 agent；也可以用 `/computer <任务>` 显式触发。
- 子 agent 只操作 GUI，不改文件、不跑命令；需要改代码时它会在报告里说明，由主 agent 修改。
- 报告格式固定为 `STATUS / SUMMARY / EVIDENCE / BLOCKERS / NEXT`。`blocked` 表示需要你介入：登录、二次验证、验证码、系统权限弹窗或需要确认的高风险动作。
- 同一时间只有一个会话能操作桌面；另一个会话会得到 `BUSY`。

## 5. 配置

### 5.1 用户策略 `~/.grok-computer/policy.json`

只有用户目录下的策略可以调整这些开关：

```json
{
  "require_subagent": true,
  "state_max_age_s": 30,
  "allow_isolated_browser": true,
  "allow_apps": ["MyApp"],
  "deny_apps": ["Internal Admin"],
  "risky_words": ["归档", "archive"]
}
```

| 键 | 默认 | 说明 |
|---|---|---|
| `require_subagent` | `true` | GUI 工具只允许 computer 子 agent 调用 |
| `state_max_age_s` | `30` | guard 认为门面状态文件仍然新鲜的秒数（1–300） |
| `allow_isolated_browser` | `true` | Playwright 以 `--isolated` 运行（无你的登录态），普通浏览器动作默认放行 |
| `subagent_types` | `computer-use:computer`、`computer` | 允许调用 GUI 工具的子 agent 类型（只能追加） |
| `allow_apps` | `[]` | 不需要逐会话确认的应用（通常是你正在开发的应用） |
| `deny_apps` / `risky_words` / `credential_words` / `dangerous_keys` / `poor_ax_apps` | 内置默认值 | 只能追加，不能删除内置项 |

### 5.2 项目策略 `.grok/computer-policy.json`

项目策略**只能追加**列表（`allow_apps`、`deny_apps`、`risky_words` 等），不能关闭 `require_subagent`、不能放宽 `state_max_age_s`、不能改 `allow_isolated_browser`、不能添加子 agent 类型；黑名单与危险快捷键（R3）永远不会被放宽。

```json
{
  "allow_apps": ["MyApp", "MyApp Dev", "Simulator"],
  "deny_apps": ["Production Console"],
  "risky_words": ["rollback", "回滚"]
}
```

策略文件格式错误时：hooks 对每个调用都改为询问（ask），门面拒绝所有动作并返回 `POLICY_ERROR`，直到文件修好。

### 5.3 宿主权限规则 `~/.grok/config.toml`

```toml
[plugins]
enabled = ["computer-use"]

[permission]
deny = ["MCPTool(cua__*)"]          # 禁止绕过门面直接调用驱动
ask = ["MCPTool(computer__drag)"]   # 按团队偏好收紧，例如拖拽一律确认
```

### 5.4 门面环境变量

在插件 `.mcp.json` 的 `computer` 服务 `env` 中设置（默认值已适合大多数人）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `GROK_COMPUTER_BACKEND` | `cua-driver` | `fake` 仅用于测试与演示 |
| `GROK_COMPUTER_MODE` | `host` | `sandbox`：操作 VM/容器里的桌面（见 5.5） |
| `GROK_COMPUTER_SCREENSHOT_LONG_EDGE` | `1280` | 规范截图长边，640–1999 |
| `GROK_PLUGIN_DATA` | 宿主提供 | 状态与轨迹目录的根（`state/`、`traces/`）；不可用时退回 `~/.grok-computer/` |
| `GROK_COMPUTER_STATE_DIR` / `GROK_COMPUTER_TRACE_DIR` | 由上项推导 | 显式指定状态 / 轨迹目录 |
| `GROK_COMPUTER_TRACE_RETENTION_DAYS` | `7` | 轨迹保留天数（1–365） |
| `GROK_COMPUTER_CUA_TRANSPORT` | `mcp` | `cli`：改用 `cua-driver call` |
| `GROK_COMPUTER_CUA_COMMAND` | `cua-driver` | 驱动命令，可包装，如 `ssh my-vm cua-driver` |
| `GROK_COMPUTER_CUA_SOCKET` | — | 驱动 daemon 端点（沙箱内） |
| `GROK_COMPUTER_CUA_FRAME_SPACE` | `auto` | 元素坐标空间，Phase 0 确认后可固定 |
| `GROK_COMPUTER_GROUNDING_PROVIDER` | 有 URL 时 `uitars`，否则 `none` | `uitars` / `xai` / `none` |
| `GROK_COMPUTER_GROUNDING_URL` / `_MODEL` / `_API_KEY` | — | grounding 端点（OpenAI 兼容）、模型名、密钥 |
| `GROK_COMPUTER_GROUNDING_TIER` | `som` | `direct` / `som` / `strict`，由 grounding 基准决定 |
| `GROK_COMPUTER_GROUNDING_COORDS` | `smart_resize` | UI-TARS 输出坐标的约定：`smart_resize` / `relative_1000` / `image` |
| `GROK_COMPUTER_SOM_REGIONS` | `0` | `1`：使用驱动可选感知扩展的文本/图标区域（可能含 AGPL 组件，需自行选装） |
| `GROK_COMPUTER_LOG_LEVEL` | `WARNING` | 日志只写 stderr |
| `GROK_COMPUTER_LOCK_DIR` | `~/.grok-computer/run` | 桌面锁目录 |

无人值守运行（CI）时在宿主进程环境中设置 `GROK_COMPUTER_ASK_AS_DENY=1`：所有本应弹窗确认的动作改为记录在案的拒绝。

### 5.5 沙箱模式

把门面指向 VM 或容器内的驱动，宿主机桌面保持不受影响：

```json
"env": {
  "GROK_COMPUTER_BACKEND": "cua-driver",
  "GROK_COMPUTER_MODE": "sandbox",
  "GROK_COMPUTER_CUA_COMMAND": "ssh my-vm cua-driver"
}
```

或用 `GROK_COMPUTER_CUA_SOCKET` 指定沙箱内 daemon 的端点。沙箱与宿主桌面使用不同的锁，可以由不同会话同时操作。guard 不区分运行模式，首次操作某个应用仍会询问；可以把沙箱里的应用写进项目策略 `allow_apps`。

## 6. 隐私与安全

- **截图会发送给模型服务商**（xAI），用于子 agent 理解界面。不要在含敏感信息的屏幕上运行任务。
- 黑名单应用（密码管理器、钥匙串、系统安全与密码设置、终端、银行/券商/钱包客户端等）的窗口不会被观测或操作；整屏截图时这些窗口被打码。
- 子 agent 不会输入密码、令牌、支付信息，遇到登录、二次验证、验证码和系统权限弹窗会停下并报告 `blocked`；安全输入框一律拒绝输入。
- 浏览器服务以 `--isolated` 运行，不带你的 Cookie 和已登录状态。
- 轨迹（每次工具调用一行 JSONL，加关键截图）和审计日志只保存在本机，默认保留 7 天；输入的文本只记录长度和前 2 个字符。清理：

```bash
uvx grok-computer-mcp@0.2.0 trace purge --all
```

- **急停**：任何时候都可以让门面拒绝所有 GUI 动作，直到恢复：

```bash
uvx grok-computer-mcp@0.2.0 stop
uvx grok-computer-mcp@0.2.0 resume
```

  宿主机模式下，前台动作期间移动物理鼠标也会让当前动作中止（`USER_INTERRUPT`）。

## 7. 故障排查

| 现象 | 原因与处理 |
|---|---|
| 报告 `PERMISSION_MISSING` | 给 CuaDriver.app 授予辅助功能 / 屏幕录制，并以 daemon 方式重启（2.1） |
| 报告 `BUSY` | 另一个会话正在操作桌面；等它结束（锁在空闲 120 秒后自动释放） |
| 报告 `POLICY_ERROR` | 策略文件不是合法 JSON 或字段类型不对；`doctor` 会指出文件与原因 |
| 报告 `USER_INTERRUPT` | 急停已开启（`resume` 清除），或前台动作期间鼠标被移动 |
| 报告 `APP_DENIED` / `SECURE_FIELD` / `KEY_DENIED` | 硬规则拦截，属于预期；需要的话请手动完成该步骤 |
| 主 agent 直接调用 GUI 工具被拒 | 预期行为：GUI 工具只允许 computer 子 agent 调用 |
| SessionStart hook 显示失败 | 查看 hook 输出的那一行原因（Python 版本、策略文件、状态目录不可写或急停开启） |
| hooks 似乎没有生效 | 确认插件已 `--trust` 且已启用（`grok inspect`）；确认 `python3` 在 PATH 中 |
| 状态写到了 `~/.grok-computer/` | 宿主没有展开 `${GROK_PLUGIN_DATA}`（V5），门面与 hooks 已自动改用该目录，功能不受影响 |

## 8. 卸载

```bash
grok plugin uninstall computer-use
uvx grok-computer-mcp@0.2.0 trace purge --all
rm -rf ~/.grok-computer
```

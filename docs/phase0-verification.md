# Phase 0 验证记录

本文记录 [goal.md 第 12 节](goal.md#12-phase-0-待验证清单) 各项假设的验证方法与结果。每项确认或被否定后，同时更新 goal.md 第 12 节、代码中对应的 `V<n>` 注释与预案路径（AGENTS.md）。

## 1. 当前状态（2026-10-04）

验证工具已就绪，尚未在真实账号与机器上运行（探针会话会产生少量费用，需要使用者自己的 grok 账号）。

| 编号 | 验证项 | 方式 | 状态 | 结果 / 证据 |
|---|---|---|---|---|
| V1 | MCP 截图以视觉 token 送达模型 | 探针 `image-canonical`（每个模型） | 待运行 | |
| V2 | 1280 px 截图未被宿主二次缩放 | 探针 `image-canonical`、`image-large` + trace 导出 | 待运行 | |
| V3 | 子 agent 内的 `PreToolUse` 含 `subagentType` | 探针 `delegate` | 待运行 | |
| V4 | 插件 agent 的 `model`/`tools`/`maxTurns`/`mcpInheritance` 生效 | `grok inspect --json` + 探针 `delegate-scope`/`delegate-turns`/`delegate-direct` | 待运行 | |
| V5 | `.mcp.json` 中 `${GROK_PLUGIN_DATA}` 等变量展开 | 探针 `env`（env 与 args 两处） | 待运行 | |
| V6 | 工具全名与 hook matcher 的实际匹配 | 探针 hook 载荷 + `^probe__` matcher | 待运行 | |
| V7 | 由 grok 拉起的驱动在 macOS 上获得系统权限 | 手工（第 3 节） | 待运行 | |
| V8 | Grok 坐标定位命中率 | `eval/grounding/run_benchmark.py` | 待运行（需先采集截图） | |
| V9 | 长任务中旧图淘汰与压缩 | 手工（第 3 节）+ 探针插件记录的 PreCompact/PostCompact | 待运行 | |
| V10 | 子 agent 后台运行时的等待体验 | 探针 `background` + 手工观察 | 待运行 | |
| V11 | grok 自身沙箱 profile 下 MCP 服务可用 | 探针 `env`（每个 `--sandbox` profile） | 待运行 | |
| V12 | 插件命令的 `$ARGUMENTS` 占位符 | 探针 `command-args`、`command-args-short` | 待运行 | |
| V13 | `SubagentStop` 载荷字段与 matcher | 探针 `delegate` | 待运行 | |
| V14 | Cua Driver 元素 frame 的坐标空间 | 手工（第 3 节） | 待运行 | |
| V15 | 宿主是否把 `structuredContent` 交给模型 | 探针 `structured` | 待运行 | |
| V16 | Cua Driver 回复字段、拒绝码位置、CLI 输出 | 手工（第 3 节） | 待运行 | |

## 2. 自动探针

### 2.1 组成

| 文件 | 作用 |
|---|---|
| `eval/phase0/probe-plugin/` | 探针插件 `computer-use-probe`：三个只用标准库的 MCP 服务（`probe`、`probe2`、`probe_rootvar`），工具 `show_code`、`structured_code`、`env`、`echo`；把每个 hook 事件的载荷（脱敏后）记入日志的 hooks；子 agent `probe`、`probe-direct`；命令 `probe-args` |
| `eval/phase0/probes.py` | 每个探针的提示词与它验证的项目 |
| `eval/phase0/run_probes.py` | 在临时工作区以项目级插件安装探针插件，每次生成新的识别码图片，逐个运行 `grok -p` 会话并导出本地 trace 与会话记录 |
| `eval/phase0/analyze.py`、`evidence.py` | 读取日志、结果与 trace 中的图片尺寸，给出 PASS / FAIL / UNKNOWN / MANUAL |

### 2.2 运行

```bash
uv run python eval/phase0/run_probes.py --out /tmp/phase0 \
    --model grok-build-0.1 --model grok-4.7 --sandbox workspace
```

- 只跑部分探针：`--only delegate,command-args`；只准备工作区、打印命令：`--dry-run`。
- 不修改用户的 grok 配置；会话记录会和其他会话一样出现在 grok 的历史中。
- trace 用 `grok trace --local` 导出，不上传。
- 结束后生成 `/tmp/phase0/report.md` 与 `report.json`；也可以单独重新分析：`uv run python eval/phase0/analyze.py /tmp/phase0`。

### 2.3 判定规则

| 项 | PASS | FAIL |
|---|---|---|
| V1 | 回答中出现图片里的识别码（识别码只存在于像素中，文本内容里没有） | 回答 `NO_IMAGE` |
| V2 | canonical 运行的 trace 中出现 1280×800 的图片 | trace 中只有其他尺寸 |
| V3 | 子 agent 调用 `echo` 时的 PreToolUse 载荷带非空 `subagentType` | 有调用记录但没有该字段 |
| V4 | 子 agent 没有调用 `probe2`（`mcpInheritance: named` 生效），且 `echo` 调用次数不超过 `maxTurns: 3` | 两者之一不成立；`tools` 能否直接列 MCP 工具单独记录在证据中 |
| V5 | `probe` 服务启动时 `GROK_COMPUTER_PROBE_DATA` 是绝对路径且不含 `${` | 值仍是字面量 `${GROK_PLUGIN_DATA}` 或为空；args 中的 `${GROK_PLUGIN_ROOT}` 是否展开看 `probe_rootvar` 服务是否启动 |
| V6 | 探针工具名形如 `probe__echo`，且 matcher `^probe__` 的 hook 被触发 | 名字带插件前缀或 matcher 未触发 |
| V10 | 后台委派后主 agent 拿到了子 agent 的 `DONE` 行 | 回答 `NOT_AVAILABLE` |
| V11 | 指定 profile 下 `env` 调用被记录 | 没有记录 |
| V12 | 输出含 `ARGS<<hello probe world>>` | 输出含未展开的 `ARGS<<$ARGUMENTS>>` |
| V13 | SubagentStop 载荷含 `lastAssistantMessage` 与 `stopHookActive`，且 matcher `probe$` 的 hook 被触发 | 缺字段或 matcher 未触发 |
| V15 | 回答中出现结构化识别码的小写形式（只有看到 structuredContent 的模型才能写出） | 回答 `NO_CODE` |

没有产生证据（会话失败、未调用工具）时为 UNKNOWN，需要查看 `transcripts/` 下的会话记录后重跑。

## 3. 手工验证步骤

### V7 macOS 权限归属

1. 按 [install.md](install.md) 2.1 给 CuaDriver.app 授权并以 daemon 运行。
2. 在 grok 会话中让子 agent `observe` 任意窗口；运行 `uvx grok-computer-mcp@0.2.0 doctor`，记录 `permission ...` 各项。
3. 停掉 daemon，设置 `GROK_COMPUTER_CUA_TRANSPORT=cli` 再试一次，记录系统是否弹出新的授权请求、请求归属于哪个进程。
4. 结论写入第 1 节：默认的 daemon 方式是否无需额外授权；直连方式是否需要给终端或 Python 授权。

### V9 长任务中的图片淘汰

1. 安装探针插件（`run_probes.py --dry-run` 生成的工作区即可），在该工作区用 grok 交互式运行一个 50 步以上的 GUI 任务（例如逐项检查设置页的每个开关）。
2. 查看 `logs/hooks.jsonl` 中的 `pre-compact` / `post-compact` 记录与时间点；用 `grok export <session>` 查看压缩后较早的截图是否还在上下文中。
3. 记录：任务成功与否、压缩次数、失败是否与引用旧截图有关。

### V14 元素坐标空间

1. 把一个窗口移到远离屏幕原点的位置（例如左上角在 (400, 300)），在 Retina 屏上更好。
2. 运行 `cua-driver call get_window_state` 读取该窗口，比较元素 `frame` 与窗口边界：落在窗口内的小数值说明是窗口坐标，与窗口位置同量级说明是屏幕点，翻倍说明是截图像素。
3. 结论确定后设置 `GROK_COMPUTER_CUA_FRAME_SPACE`（`window_points` / `screen_points` / `capture_pixels`），并删除 `coords.py` 中 V14 自动判定的依赖说明。

### V16 驱动回复格式

1. 对真实驱动分别调用 `get_window_state`、一次点击、一次被拒绝的动作（例如对后台窗口做不支持的后台投递）。
2. 对照 `servers/grok-computer-mcp/src/grok_computer_mcp/backend/cua_parse.py` 中标注 V16 的字段名、拒绝码位置与 CLI 输出形状。
3. 不一致处修改解析器并补充 `tests/fake_cua_driver.py` 的回复样例。

## 4. Grounding 基准（V8）

步骤见 [eval/README.md](../eval/README.md) 的"grounding 基准"一节：采集 → 人工确认 → 运行三个模型 → 按 goal.md 6.3 得出档位与子 agent 默认模型。结果（命中率、延迟、单次成本、档位）填入第 1 节，并写回插件 `.mcp.json` 与 `agents/computer.md` 的 `model`。

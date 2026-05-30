# claude-radar — Spec

> 多 Claude Code session 实时看板。一眼看清所有窗口在做什么、谁在等你回复。
> 开源项目（MIT），目标发布到 GitHub。

---

## 1. Problem

用户同时开多个 Claude Code session（数据分析、开发、文档审查等），分散在不同 tmux session 或独立终端。
痛点：
- 不知道哪个 session 在跑、哪个在等回复
- 不记得某个 session 在做什么
- 切来切去找当前能动的窗口，浪费时间

现有方案不足：
- Claude Code 官方无 CLI 跨 session 状态视图
- `tmux-agent-status` 把"Claude 进程存在"等同于 working，无法区分"等用户输入"，且容错差
- macOS Notification 是即时的，无总览

---

## 2. Goal

提供一个独立的 CLI 工具：
1. 实时显示所有 Claude Code session 的状态（working / waiting / idle）
2. 显示每个 session 当前在做什么（最近一次用户消息的摘要）
3. 显示状态持续时长（已经等了 X 分钟 / 已经跑了 Y 分钟）
4. 跨 tmux 内 / 外的 session 都能感知

非目标（Phase 1 不做）：
- 跳转到对应 session（Phase 2）
- 推送通知（Phase 2）
- LLM 总结（Phase 3）

---

## 3. User Story

```
$ claude-radar
┌─ Claude Sessions ─────────────────────── 15:23 ─┐
│                                                  │
│ 💬 data-analysis    线下新客归因          13m    │
│ 💬 meta             窗口管理方案讨论       5m    │
│ ⚡ dev              重构 report_utils     41m    │
│ ○  review           -                            │
│                                                  │
│ q to quit, r to refresh                          │
└──────────────────────────────────────────────────┘
```

- 💬 = 等用户回复（红/橙色）
- ⚡ = 正在执行（黄色）
- ○ = idle（灰色）
- 排序：waiting > working > idle，组内按时长降序

启动：任意终端跑 `claude-radar`，每 2 秒刷新；`q` 退出。

---

## 4. Architecture

```
┌──────────────────┐                ┌──────────────────┐
│ Claude Code      │                │ Claude Code      │
│ session A        │                │ session B        │
└────┬─────────────┘                └────┬─────────────┘
     │ hooks                             │ hooks
     │ (UserPromptSubmit / Stop /        │
     │  Notification)                    │
     ↓                                   ↓
┌────────────────────────────────────────────────┐
│ ~/.claude-radar/state/<session_id>.json        │
│ (one file per Claude Code session)             │
└──────────┬─────────────────────────────────────┘
           │ read
           ↓
┌────────────────────┐         ┌────────────────────┐
│ claude-radar       │         │ claude-radar-status│
│ (curses TUI)       │         │ (one-shot stdout)  │
└────────────────────┘         └────────────────────┘
```

---

## 5. Data Format

### 5.1 State file
路径：`~/.claude-radar/state/<session_id>.json`

```json
{
  "session_id": "data-analysis",
  "tmux_session": "data",
  "tty": "/dev/ttys023",
  "cwd": "/Users/chengxuelin/tuhu/analyses",
  "status": "waiting",
  "current_task": "线下新客归因：新客 -14.20%，是门店流量少了还是进店转化率下降",
  "task_started_at": "2026-04-25T14:32:11+09:00",
  "status_changed_at": "2026-04-25T15:01:43+09:00",
  "last_user_prompt_at": "2026-04-25T14:32:11+09:00",
  "last_assistant_stop_at": "2026-04-25T15:01:43+09:00"
}
```

### 5.2 Status enum

| status | 含义 | 触发 |
|---|---|---|
| `working` | Claude 正在响应 | UserPromptSubmit hook |
| `waiting` | 已回复，等用户 | Stop hook 或 Notification hook |
| `idle` | 长时间无活动（>30min） | render 时计算，不写入文件 |

### 5.3 Session ID 推导

```python
def get_session_id():
    # 优先：tmux session name
    tmux = subprocess.run(["tmux", "display-message", "-p", "#S"], ...)
    if tmux.returncode == 0:
        return tmux.stdout.strip()
    # fallback: TTY 编号
    return Path(os.ttyname(sys.stdout.fileno())).name  # ttys023
```

---

## 6. Components

### 6.1 `hooks/state-tracker.sh`

接受参数 `UserPromptSubmit | Stop | Notification`，从 stdin 读 JSON（Claude Code hook 协议）。

```bash
#!/usr/bin/env bash
HOOK_TYPE="$1"
STATE_DIR="${CLAUDE_RADAR_HOME:-$HOME/.claude-radar}/state"
mkdir -p "$STATE_DIR"

# 读 stdin（hook 协议要求）
STDIN_JSON=$(cat)

# 推导 session id
if [ -n "${TMUX:-}" ]; then
    SESSION_ID=$(tmux display-message -p '#S' 2>/dev/null)
fi
[ -z "$SESSION_ID" ] && SESSION_ID="tty-$(tty | sed 's|/dev/||')"

STATE_FILE="$STATE_DIR/${SESSION_ID}.json"
NOW=$(date -Iseconds)

case "$HOOK_TYPE" in
    UserPromptSubmit)
        # 从 stdin 提取用户消息（前 80 字）
        USER_MSG=$(echo "$STDIN_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('prompt','')[:80])")
        # 写状态：working + 任务名
        ;;
    Stop|Notification)
        # 写状态：waiting
        ;;
esac
```

调用 Python 辅助脚本写 JSON（保持原子性）：

```bash
python3 "$RADAR_LIB/state.py" set --session "$SESSION_ID" --status working --task "$USER_MSG"
```

### 6.2 `claude_radar/state.py`

封装状态文件的读写。提供 CLI：

```bash
python -m claude_radar.state set --session X --status working --task "..."
python -m claude_radar.state list   # 输出所有 session 的 JSON
```

读写需注意：
- 用 `os.replace` 保证原子写入
- `set` 时保留旧字段（如 task_started_at 在 working→waiting 不变）
- session 文件没有时新建

### 6.3 `claude_radar/render.py`

负责一次性渲染 statusline 字符串和 TUI 布局。

```python
def render_compact(states: list) -> str:
    """单行 statusline 输出，给 tmux status-right 用"""

def render_board(states: list, width: int, height: int) -> list[str]:
    """看板的多行输出，给 curses 渲染用"""
```

### 6.4 `bin/claude-radar`

curses TUI，2 秒刷新。键盘：
- `q` / `Esc` 退出
- `r` 立即刷新
- `c` 清理 idle session 文件（>24h 未活动）

### 6.5 `bin/claude-radar-status`

一次性输出，stdout 单行格式化字符串，适合 tmux status-right：

```bash
$ claude-radar-status
💬2 ⚡1 ○1
```

或 verbose：

```bash
$ claude-radar-status --verbose
💬 data:归因 13m | 💬 meta:讨论 5m | ⚡ dev:重构 41m
```

---

## 7. Install / Uninstall

### 7.1 `install.sh`

```bash
#!/usr/bin/env bash
set -e

INSTALL_DIR="${CLAUDE_RADAR_HOME:-$HOME/.claude-radar}"
SETTINGS_FILE="$HOME/.claude/settings.json"

# 1. clone or pull
if [ -d "$INSTALL_DIR" ]; then
    cd "$INSTALL_DIR" && git pull
else
    git clone https://github.com/<user>/claude-radar.git "$INSTALL_DIR"
fi

# 2. 备份并注入 hooks
cp "$SETTINGS_FILE" "$SETTINGS_FILE.backup-$(date +%s)"
python3 "$INSTALL_DIR/install/inject-hooks.py" "$SETTINGS_FILE"

# 3. 提示 PATH
echo "Add to your shell rc:"
echo "  export PATH=\"\$HOME/.claude-radar/bin:\$PATH\""
```

### 7.2 hook 注入逻辑

`install/inject-hooks.py` 读取 settings.json，幂等地添加 4 个 hook，已存在则跳过。

```python
HOOKS_TO_INJECT = {
    "UserPromptSubmit": "~/.claude-radar/hooks/state-tracker.sh UserPromptSubmit",
    "Stop": "~/.claude-radar/hooks/state-tracker.sh Stop",
    "Notification": "~/.claude-radar/hooks/state-tracker.sh Notification",
}
```

### 7.3 `uninstall.sh`

移除注入的 hook，删除 `~/.claude-radar/`。

---

## 8. tmux 集成（可选，README 里说明）

```tmux
# 在 tmux statusline 显示 Claude session 状态
set -g status-right "#(claude-radar-status) %m-%d %H:%M"
set -g status-interval 5
```

---

## 9. Project Layout

```
claude-radar/
├── README.md              (中英双语)
├── LICENSE                (MIT)
├── install.sh
├── uninstall.sh
├── pyproject.toml         (Python 包元信息，无第三方依赖)
├── hooks/
│   └── state-tracker.sh
├── bin/
│   ├── claude-radar       (Python 脚本，shebang)
│   └── claude-radar-status
├── claude_radar/
│   ├── __init__.py
│   ├── state.py
│   ├── render.py
│   ├── tui.py             (curses 主循环)
│   └── cli.py
├── install/
│   └── inject-hooks.py
├── tests/
│   ├── test_state.py
│   └── test_render.py
└── docs/
    ├── architecture.md
    └── screenshots/
```

---

## 10. 测试要求

**单元测试：**
- `state.py`：set/get/list 各路径，原子写入，旧字段保留
- `render.py`：状态排序、时长格式化、宽度截断、表情符号

**集成测试：**
- 模拟多个 session 的状态文件，跑 `claude-radar-status`，验证输出格式
- `inject-hooks.py` 幂等性：跑两次结果一致

**手工测试：**
- 在两个 tmux session 里各开一个 Claude，跑 `claude-radar`，验证状态切换正确
- 验证非 tmux 终端也被识别（fallback 到 tty）
- 异常退出 Claude（kill -9），看 session 状态是否被卡住；如卡住，提供 `claude-radar --reset` 命令清理

---

## 11. 交付标准（一次性完成，不分阶段）

**v0.1 必须全部齐备才算完工**：

代码：
- [ ] `claude_radar/state.py` + 测试通过
- [ ] `claude_radar/render.py` + 测试通过
- [ ] `claude_radar/tui.py`（curses 主循环）
- [ ] `hooks/state-tracker.sh` 处理三种 hook（UserPromptSubmit / Stop / Notification）
- [ ] `bin/claude-radar`（持续刷新看板）
- [ ] `bin/claude-radar-status`（一次性输出，含 `--verbose`）
- [ ] `install/inject-hooks.py`（幂等注入 hook）
- [ ] `install.sh` + `uninstall.sh`

配套：
- [ ] `README.md`（中英双语，含截图/asciinema GIF）
- [ ] `LICENSE`（MIT）
- [ ] `pyproject.toml`
- [ ] `tests/` 全部通过
- [ ] `docs/architecture.md`

端到端验收（必须实测，不只看代码）：
1. `bash install.sh` 一次成功，hook 正确注入到 `~/.claude/settings.json`
2. 同时开三个 Claude Code session（建议 tmux 内 + tmux 外混合），跑 `claude-radar`，能看到三个 session 各自的状态、任务名、时长
3. 在某 session 发消息 → 看板立刻显示 working；Claude 回完 → 看板显示 waiting
4. `claude-radar-status --verbose` 输出格式与设计一致
5. 异常退出某 session 的 Claude → 看板正确反映（卡住时 `--reset` 能修）
6. `bash uninstall.sh` 完全清理，settings.json 恢复

提交：
- 一个完整可推到 GitHub 的 repo（私有 / 公开都先初始化）
- commit 历史清晰，每个文件一次或几次有意义提交

**不可接受**：
- 「先实现核心，安装脚本下次做」
- 「测试以后补」
- 「README 只写英文」
- 「screenshots 占位先空」

测试和文档与代码同时交付。

---

## 12. README 大纲

```
# claude-radar

> Real-time dashboard for multiple Claude Code sessions.

[GIF demo]

## Why

(简短陈述痛点)

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/<user>/claude-radar/main/install.sh | bash
```

## Usage

(命令、截图)

## How It Works

(架构图，2 段说明)

## Configuration

(自定义选项、tmux 集成)

## Roadmap

- [x] v0.1 multi-session board
- [ ] v0.2 menu bar widget (macOS)
- [ ] v0.3 periodic LLM summary

## Contributing

## License
MIT
```

---

## 13. 风险 & Open Questions

1. **Claude Code hook 协议变化**：当前 hook stdin JSON 格式可能升级。需在 README 标注测试过的 Claude Code 版本。
2. **session_id 冲突**：两个不同终端开同名 tmux session（不可能，但用户重命名时 hook 已写状态文件 — 旧文件孤立）。需 `--reset` 命令。
3. **多 pane 的 tmux session**：一个 tmux session 内多个 pane 各跑一个 Claude，session_id 相同 — 状态会互相覆盖。Phase 1 接受这个限制（用 TMUX_PANE 区分需要 v0.2 重做存储模型）。
4. **macOS bash 3.2**：hooks 用 bash 但需避免 `declare -A`、`mapfile` 等 4+ 特性。
5. **首次启动无状态**：还没收到任何 hook 时，看板应显示空状态（友好提示，而不是空白）。

---

## 14. License

MIT。Author: chengxuelin (or pseudonym).

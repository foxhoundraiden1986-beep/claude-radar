# claude-radar

> 多 Claude Code session 实时看板。一眼看清哪个窗口在跑、哪个在等你回复、各自在做什么。

[English](./README.md)

```
╭─ Claude Sessions ──────────── 💬2 ⚡1 ○1 ─────────── 15:23 ─╮
     session             task                              age
─────────────────────────────────────────────────────────────
 💬  meta                window-manager design                5m
▶💬  data-analysis       attribution debug                   13m
 ⚡  dev                 refactor report_utils               41m
 ○   review              -

q quit · r refresh · c cleanup · ↑↓ select · ⏎ jump · i mute
```

`💬` 等你回复 · `⚡` Claude 正在跑 · `○` 闲置  
`▶` 选中光标 · session id 显示**绿色** = 当前 attached 的 tmux session

> 演示：<a href="docs/screenshots/demo.cast">docs/screenshots/demo.cast</a>
> （asciicast v3，8.4 KB）。本地播放：
> `asciinema play docs/screenshots/demo.cast`。

---

## 为什么需要这个

同时开三五个 Claude Code session（数据分析、写代码、写文档），最大的烦恼
是不知道**哪个**已经回完、在等你下一步。现有方案要么把"Claude 进程还在"
当作"还在跑"（错的：跑完和等输入是两回事），要么靠 macOS 通知逐条弹（看
不到全局）。

`claude-radar` 是一个非常小的 CLI：挂在 Claude Code 的
`UserPromptSubmit` / `Stop` / `Notification` 三个 hook 上，每个 session
对应 `~/.claude-radar/state/` 下一个 JSON 文件，再用 curses 把它们渲染
成一个看板。你可以把它扔在 tmux 的某个角落一直开着。

---

## 安装

克隆仓库，跑安装脚本：

```bash
git clone https://github.com/foxhoundraiden1986-beep/claude-radar.git ~/.claude-radar
bash ~/.claude-radar/install.sh
```

把 bin 目录加到 `PATH`：

```bash
export PATH="$HOME/.claude-radar/bin:$PATH"
```

然后**重启所有正在跑的 Claude Code session**，让新 hook 加载进去。完事。

安装脚本会做三件事：

1. 把项目文件复制到 `$CLAUDE_RADAR_HOME`（默认 `~/.claude-radar`）。
2. 把 `~/.claude/settings.json` 备份到 `settings.json.backup-<时间戳>`。
3. 幂等地往 settings.json 里塞三个 hook（`UserPromptSubmit` / `Stop` /
   `Notification`）。重复跑也不会塞重复。

如果想往别的 settings 文件里注入（比如 CI、开发沙箱）：

```bash
bash install.sh --settings /path/to/settings.json --install-dir /opt/cr
```

加 `--no-hooks` 只复制文件、不动 settings。

### 环境要求

- Python ≥ 3.9（仅依赖标准库 — `pyproject.toml` 的运行时依赖为空，
  `curses` 是 stdlib）
- `bash` ≥ 3.2（macOS 自带的就够）
- 终端能显示 emoji 和中文（绝大多数都能）

---

## 使用

### 看板

```bash
claude-radar
```

打开全屏看板，每两秒自动刷新。

| 按键               | 动作                                                  |
| ------------------ | ----------------------------------------------------- |
| `q`、`Esc`         | 退出                                                  |
| `r`                | 立即刷新                                              |
| `c`                | 清掉超过 24 小时没活动的 state 文件                   |
| `↑` `↓` / `k` `j`  | 移动选中光标                                          |
| `⏎` Enter          | 跳转到选中的 tmux session（在 tmux 内调 `switch-client`；看板跑在 tmux 外时通过 osascript 弹 Terminal/iTerm 新窗口） |
| `i`                | 静音选中 session — 显示为 idle，直到它真实状态再次变化时自动解除 |

某个 session 卡住了（被 `kill -9` 之类，`Stop` hook 没触发到）？看板里按
`c` 即可；命令行里也可以：

```bash
claude-radar --reset      # 删掉所有 state 文件
claude-radar --cleanup    # 只删超过 24 小时的
claude-radar --once       # 输出一帧到 stdout，不开 curses（适合 CI）
```

### 一次性输出（给 tmux statusline / 提示符 / 脚本用）

```bash
$ claude-radar-status
💬2 ⚡1 ○1

$ claude-radar-status --verbose
💬 data:归因 13m | 💬 meta:讨论 5m | ⚡ dev:重构 41m
```

### 接入 tmux

```tmux
# ~/.tmux.conf
set -g status-right "#(claude-radar-status) %m-%d %H:%M"
set -g status-interval 5
```

---

## 工作原理

```
┌──────────────┐                       ┌──────────────┐
│ Claude Code  │                       │ Claude Code  │
│ session A    │                       │ session B    │
└──────┬───────┘                       └──────┬───────┘
       │ UserPromptSubmit / Stop / Notification
       ↓                                      ↓
┌──────────────────────────────────────────────────────┐
│ ~/.claude-radar/state/<session_id>.json              │
│   （每个 Claude Code session 一份小 JSON）           │
└────────────────────┬─────────────────────────────────┘
                     │ 读
                     ↓
            ┌────────────────────┐
            │ claude-radar (TUI) │
            │ claude-radar-status│
            └────────────────────┘
```

每个 Claude Code session 用 tmux session name 作为 ID（不在 tmux 里就
用控制 tty）。Hook 脚本把当前状态（`working` / `waiting`）和最近一条用
户输入（作为"任务名"）原子写入 state 文件（`os.replace`）。渲染器每次
刷新就把所有 state 文件读一遍——没有 daemon、没有共享内存、没有 socket。
state 文件很小，几百个 session 也不会有压力。

更详细的设计取舍（包括"为什么不写一个 daemon"、多 pane 的局限等）见
[`docs/architecture.md`](./docs/architecture.md)。

---

## 配置

| 环境变量             | 默认值               | 用途                                  |
| -------------------- | -------------------- | ------------------------------------- |
| `CLAUDE_RADAR_HOME`  | `~/.claude-radar`    | state 文件和 hook 脚本所在目录        |

其他行为大多用命令行参数控制——`claude-radar --help` 和
`claude-radar-status --help` 里都有。

### 卸载

```bash
bash ~/.claude-radar/uninstall.sh --purge --purge-state
```

`--purge` 把安装目录一起删掉；`--purge-state` 把 state JSON 也清掉。两
个都不加的话，脚本只把 settings.json 里的我们这几个 hook 摘掉（顺便备份
一下）。

---

## 兼容性

- **Claude Code**：基于 2025 年末 / 2026 年初的 hook 协议。Hook payload
  从 stdin 读，用户输入字段名假定为 `prompt`。如果将来 Claude Code 改
  字段名，只要改 `hooks/state-tracker.sh` 一个文件即可。
- **macOS**：Sequoia / Sonoma，自带 `bash 3.2`，已验证。
- **Linux**：Ubuntu 22.04 + `bash 5.x` 已验证。

---

## 已知限制

- **`uninstall.sh` 会重排 `settings.json` 的格式。** 我们摘掉 hook 后会用
  两格缩进重新序列化整个文件，所以你原本手工压缩的紧凑数组（比如
  `"allow": ["Read"]`）会被展开成多行。语义完全等价；卸载前总会留一份
  `.backup-<时间戳>`，需要原字节找回来即可。
- **同一个 tmux session 下多个 pane 共用一行。** 如果你在同一个 tmux
  session 的两个 pane 各开一个 Claude Code，它们会共享 state 文件、互相
  覆盖。v0.2 计划用 `$TMUX_PANE` 加进 session id 解决。
- **找不到 `python3` 时 hook 静默不动作。** hook 绝不能让 Claude Code 崩，
  所以一旦 PATH 上没 Python，脚本直接 exit 0，看板就看不到更新而已。

---

## Roadmap

- [x] **v0.1** — 多 session 看板、一次性 status、安装/卸载脚本、跳转、静音、
  状态计数、长任务换行、当前 attached 行高亮
- [ ] **v0.2** — macOS 菜单栏小组件、按 pane 区分 session（一行对应一个 tmux
  pane 而不是 session）、定期 LLM 摘要
- [ ] **v0.3** — 长任务定时 LLM 摘要
- [ ] **v0.4** — 历史回看（一小时前各个 session 在做什么？）

---

## 贡献

欢迎提 issue 和 PR。本地跑测试：

```bash
python3 -m unittest discover -s tests -v
```

项目刻意没有第三方依赖，如果你打算 `pip install` 什么东西，先开个 issue
聊一下。

---

## 许可

MIT。见 [LICENSE](./LICENSE)。

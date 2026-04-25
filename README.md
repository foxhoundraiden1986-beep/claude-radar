# claude-radar

> Real-time dashboard for multiple Claude Code sessions. One glance shows
> which window is running, which is waiting on you, and what each one is
> working on.

[简体中文](./README.zh-CN.md)

```
─ Claude Sessions ──────────────────────────────────── 15:23 ─

💬  data-analysis     线下新客归因                            13m
💬  meta              窗口管理方案讨论                         5m
⚡  dev               重构 report_utils                       41m
○   review            -

q quit · r refresh · c cleanup
```

`💬` waiting on you · `⚡` Claude is working · `○` idle

> Demo: <a href="docs/screenshots/demo.cast">docs/screenshots/demo.cast</a>
> (asciicast v3, 8.4 KB). Play locally with
> `asciinema play docs/screenshots/demo.cast`.

---

## Why

Working with multiple Claude Code sessions in parallel — one for analysis,
one for code, one for docs — leaves you constantly hunting for *the* window
that's done thinking and is waiting on a reply. Existing solutions either
look at "is the Claude process alive" (wrong: that conflates running and
waiting) or fire one-shot macOS notifications (no overview).

`claude-radar` is a tiny CLI that hooks into Claude Code's
`UserPromptSubmit` / `Stop` / `Notification` events, writes one JSON file
per session under `~/.claude-radar/state/`, and renders that as a curses
dashboard you can leave running in a corner of your tmux layout.

---

## Install

Clone and run the installer:

```bash
git clone https://github.com/chengxuelin/claude-radar.git ~/.claude-radar
bash ~/.claude-radar/install.sh
```

Then add the bin directory to your `PATH`:

```bash
export PATH="$HOME/.claude-radar/bin:$PATH"
```

Restart any running Claude Code sessions so the new hooks load. That's it.

The installer:

1. Copies the project files into `$CLAUDE_RADAR_HOME` (default
   `~/.claude-radar`).
2. Backs up `~/.claude/settings.json` to `settings.json.backup-<ts>`.
3. Idempotently injects three hook entries (`UserPromptSubmit`, `Stop`,
   `Notification`). Re-running the installer never duplicates them.

To install hooks into a custom settings file (handy in CI / dev sandboxes):

```bash
bash install.sh --settings /path/to/settings.json --install-dir /opt/cr
```

`--no-hooks` will install the files without touching any settings file.

### Requirements

- Python ≥ 3.9 (stdlib only — `pyproject.toml` has zero runtime
  dependencies; `curses` is part of the standard library)
- `bash` ≥ 3.2 (the macOS default works)
- A terminal that can display emoji and CJK characters (most do)

---

## Usage

### Dashboard

```bash
claude-radar
```

Opens a full-screen curses dashboard that refreshes every two seconds.

| key       | action                                                   |
| --------- | -------------------------------------------------------- |
| `q`, `Esc`| quit                                                     |
| `r`       | refresh immediately                                      |
| `c`       | delete state files older than 24h                        |

Need to recover from a stuck session (Claude got `kill -9`'d, hooks never
fired `Stop`)? Either press `c` inside the TUI, or run:

```bash
claude-radar --reset      # nuke all state files
claude-radar --cleanup    # only the >24h-stale ones
claude-radar --once       # one snapshot to stdout, no curses (good for CI)
```

### One-shot status (for tmux statusline, prompts, scripts)

```bash
$ claude-radar-status
💬2 ⚡1 ○1

$ claude-radar-status --verbose
💬 data:归因 13m | 💬 meta:讨论 5m | ⚡ dev:重构 41m
```

### tmux integration

```tmux
# ~/.tmux.conf
set -g status-right "#(claude-radar-status) %m-%d %H:%M"
set -g status-interval 5
```

---

## How It Works

```
┌──────────────┐                       ┌──────────────┐
│ Claude Code  │                       │ Claude Code  │
│ session A    │                       │ session B    │
└──────┬───────┘                       └──────┬───────┘
       │ UserPromptSubmit / Stop / Notification
       ↓                                      ↓
┌──────────────────────────────────────────────────────┐
│ ~/.claude-radar/state/<session_id>.json              │
│   (one tiny JSON file per Claude Code session)       │
└────────────────────┬─────────────────────────────────┘
                     │ read
                     ↓
            ┌────────────────────┐
            │ claude-radar (TUI) │
            │ claude-radar-status│
            └────────────────────┘
```

Each Claude Code session is identified by its tmux session name (or its
controlling tty if no tmux). The hook script writes the current status
(`working` / `waiting`) and the most recent user prompt as the "task name"
to the state file atomically (via `os.replace`). The renderer reads every
state file on every refresh — there is no daemon, no shared memory, no
socket. State files are cheap to read and small enough that even a hundred
sessions wouldn't matter.

See [`docs/architecture.md`](./docs/architecture.md) for the in-depth
walkthrough, including the design choices behind state ownership and the
known multi-pane caveat.

---

## Configuration

| Environment variable | Default              | Purpose                              |
| -------------------- | -------------------- | ------------------------------------ |
| `CLAUDE_RADAR_HOME`  | `~/.claude-radar`    | Where state files and hooks live.    |

Most behavior is controlled by command-line flags — see `claude-radar
--help` and `claude-radar-status --help`.

### Uninstall

```bash
bash ~/.claude-radar/uninstall.sh --purge --purge-state
```

`--purge` removes the install directory, `--purge-state` wipes the JSON
state files. Without either flag the script just removes our hook entries
from `settings.json` (and leaves a backup).

---

## Tested Against

- **Claude Code**: hook protocol as of late 2025 / early 2026. The hook
  payload is read from stdin and the field name `prompt` is used for the
  user message. If a future Claude Code release renames that field, only
  `hooks/state-tracker.sh` needs an update.
- **macOS**: Sequoia / Sonoma, default `bash 3.2`.
- **Linux**: tested on Ubuntu 22.04 with `bash 5.x`.

---

## Limitations

- **`uninstall.sh` reformats `settings.json`.** When we strip our hooks we
  re-serialise the JSON with two-space indentation, so any pre-existing
  hand-formatting (compact arrays, custom indentation) will normalise to
  our style. Semantically equivalent in every case; a `.backup-<unix>`
  file is written first so you can recover the exact bytes if needed.
- **One tmux session = one row, even with multiple panes.** If you run
  two Claude Code sessions in panes of the same tmux session, they will
  share a state file and overwrite each other. Tracked for v0.2 (will
  include `$TMUX_PANE` in the session id).
- **Hook silently no-ops if `python3` is unavailable.** Hooks must not
  break Claude Code; if no Python is on `PATH` the script exits 0 and
  the dashboard simply won't see updates.

---

## Roadmap

- [x] **v0.1** — multi-session board, one-shot status, install / uninstall
- [ ] **v0.2** — menu-bar widget (macOS), per-pane sessions, `j` jump-to
      session
- [ ] **v0.3** — periodic LLM summary of long-running tasks
- [ ] **v0.4** — windowed history (what was each session doing 1h ago?)

---

## Contributing

Bug reports and PRs welcome. Run the test suite locally with

```bash
python3 -m unittest discover -s tests -v
```

There are no third-party dependencies; if you find yourself reaching for
`pip install`, please open an issue first so we can talk it through.

---

## License

MIT. See [LICENSE](./LICENSE).

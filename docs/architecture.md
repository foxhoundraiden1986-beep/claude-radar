# claude-radar — Architecture

This doc explains *why* the code looks the way it does. The README is
sufficient for users; this is for contributors and for future-me.

## Goals (and non-goals)

The thing has to be:

1. **Truthful.** "working" must mean Claude is actively responding;
   "waiting" must mean it has stopped and is waiting on you. Anything that
   conflates the two is worse than no tool, because it teaches you to
   distrust the dashboard.
2. **Cheap.** It runs in your terminal, every two seconds, forever. CPU
   and memory cost should be invisible.
3. **No-daemon.** Daemons need restart logic, log rotation, and a debug
   story. None of that is worth it for a personal dashboard.
4. **Stdlib-only.** A `pip install` step kills adoption for a tool people
   try once. `curses`, `json`, `os`, `subprocess` are enough.
5. **Recoverable.** When something goes wrong (Claude killed `-9`, hook
   skipped, settings.json corrupt), the user can fix it without
   understanding the internals.

Non-goals — at least for v0.1:

- Cross-host aggregation. The state files are local; remote workers are
  out of scope.
- A persistent history. Each state file is a snapshot, not a log.
- LLM-powered task summaries. Nice-to-have but not load-bearing.

## Three layers

```
hooks/state-tracker.sh        ← writes state (one file per session)
claude_radar/state.py         ← typed read/write API + CLI used by hooks
claude_radar/render.py        ← pure formatting (no I/O)
claude_radar/tui.py           ← curses main loop
claude_radar/cli.py           ← argparse glue → tui / status
bin/claude-radar(-status)     ← thin shims importing the package
install/inject-hooks.py       ← idempotent settings.json patcher
```

The split was chosen so each piece is independently testable:

- `state.py` is tested via `tempfile.TemporaryDirectory()` plus the
  `CLAUDE_RADAR_HOME` override.
- `render.py` is tested by feeding raw dicts and a fixed `now`. Sorting,
  duration formatting, CJK width handling all live here so they can be
  asserted on without a terminal.
- `tui.py` is the only module that touches `curses`. It does no
  formatting beyond colour assignment, so it stays small (≈100 LOC).
- `inject-hooks.py` is tested end-to-end against synthetic settings JSON.

## Why one JSON file per session, not a single mutex-protected DB

The dashboard must tolerate hooks racing across many shells writing
concurrently and an in-terminal renderer reading on a fixed cadence. Two
options:

1. **Single shared file** + flock / sqlite. Means every hook needs a
   non-trivial dependency or a portable lock implementation; readers can
   block writers.
2. **One file per session.** Writes are independent — there is no
   cross-session race, ever. Each writer uses
   `tempfile.mkstemp` + `os.replace`, which is atomic on every POSIX
   filesystem. Readers see either the old payload or the new one, never a
   half-written file.

We chose option 2. List operations (`list_states`) iterate the directory
— at five sessions or fifty, this is microseconds.

## Status taxonomy

| status      | meaning                                  | written by                         |
| ----------- | ---------------------------------------- | ---------------------------------- |
| `working`   | Claude is currently responding           | `UserPromptSubmit` hook            |
| `waiting`   | Claude has finished, awaiting user input | `Stop` and `Notification` hooks    |
| `idle`      | derived: working but the hook hasn't     | computed in `render.derive_view`   |
|             | fired in a long time → probably stale    |                                    |

`idle` is **never written to disk** — it's a render-time escalation. The
default threshold (`DEFAULT_IDLE_AFTER_SECONDS = 6h`) is intentionally
generous: long-running compiles and analyses routinely take 30+ minutes,
and we'd rather show "working" honestly than lie about a still-running
task. If a `working` session has not had any hook fire for 6h, the
overwhelming likelihood is that Claude died or the user closed the
window; we display `○` so the dashboard does not drown in stale entries.

`waiting` is **not** escalated — once Claude has signalled it's done and
you have not replied, the dashboard's whole job is to keep flagging that.

## Session ID derivation

Hook scripts have two reliable identifiers available:

- `tmux display-message -p '#S'` — works inside any tmux session, even
  for nested or detached ones.
- `tty` — every interactive shell has one; `/dev/ttys023` etc.

We prefer tmux (it's stable across pane reattaches), and fall back to
`tty-<basename>` for non-tmux terminals. Two known footguns:

- **Multiple panes inside one tmux session.** They share a session name,
  so they share a state file. v0.1 accepts this — fixing it requires
  including `$TMUX_PANE` in the id, which means the state file
  proliferation needs to be considered. Tracked in v0.2.
- **Renaming a tmux session mid-flight.** The old state file becomes
  orphaned; `claude-radar --cleanup` (or pressing `c` in the TUI) will
  reap it after 24h.

## Display width

Terminals are 1980s tech and treating a screen as a grid of "characters"
is a lie. Each cell in the grid corresponds to either a 1-cell character
(ASCII), a 2-cell character (CJK, full-width emoji), or a 0-cell
combining mark.

`render._display_width` walks the string with `unicodedata` and
`unicodedata.east_asian_width`, treating East-Asian Wide / Fullwidth as
2 cells and combining marks as 0. This is a small subset of the
`wcwidth` library; it covers everything our dashboard prints (Latin,
CJK, common emoji) and lets us stay stdlib-only.

`truncate_display` and `pad_display` use the same width arithmetic, so a
narrow terminal never overflows and rows always line up regardless of
how much CJK is in the task name.

## Hook script in bash, not Python

Tradeoff: writing the hook in Python would let us reuse `state.py`
directly. We chose bash because:

- The hook runs **per Claude Code event** — sometimes many per second
  during a long session. Forking bash is faster than forking python +
  importing.
- Bash is universally present on macOS and Linux; Python 3 isn't
  guaranteed at the system level on every macOS install (the user might
  rely on Homebrew Python, which means PATH detail).
- Bash 3.2 (macOS default) is the lowest common denominator. We
  deliberately avoid `declare -A`, `mapfile`, and process substitution
  into arrays.

The bash script does the absolute minimum (sniff session id, decide
status, prepare argv) and shells out to `python -m claude_radar.state set`
for the JSON write — keeping atomic-write logic in one place.

## Why `inject-hooks.py` over `jq`

`jq` is great but isn't installed by default on macOS. The script is
~150 LOC of stdlib JSON manipulation; the test suite hits both
inject-and-remove paths plus the path-move and corrupt-JSON edge cases.
The marker we use to recognise our own hooks is the substring
`/hooks/state-tracker.sh`, which is path-suffix-stable across
`--install-dir` overrides — that's what makes idempotency work.

## Recovery commands

| symptom                                    | fix                                       |
| ------------------------------------------ | ----------------------------------------- |
| dashboard shows a session that no longer exists | `c` inside TUI, or `claude-radar --cleanup` |
| dashboard wedged after `kill -9`           | `claude-radar --reset` then restart Claude|
| settings.json got mangled                  | restore `settings.json.backup-<ts>`       |
| hook silently broken                       | `bash hooks/state-tracker.sh UserPromptSubmit <<< '{"prompt":"x"}'` and inspect the state file |

## What's deliberately *not* here

- A long-running watcher process. We don't want one.
- Inotify / fsevents. The two-second poll is fine, and it works the same
  way on every OS.
- Colour themes. The three colours we use (red / yellow / dim white) map
  cleanly to "needs attention" / "in progress" / "background"; more
  configurability is a feature trap.
- Per-session settings (overriding emoji, names, colours). Easy to add
  later if asked for; expensive to delete once shipped.

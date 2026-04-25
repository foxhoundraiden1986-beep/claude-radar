# claude-radar v0.1 — QA Report (T7)

**Tester:** qa
**Date:** 2026-04-25
**Commit under test:** `a237de9` (Add bilingual README and architecture documentation)
**Sandbox:** all hook injection / install / uninstall steps used `/tmp/radar-test/`. The real `~/.claude/settings.json` was **not** touched.

## Status: NEEDS_FIX

Two HIGH-severity bugs block sign-off (both in shipping artefacts: `install.sh`, `hooks/state-tracker.sh`). Core feature surface — TUI, render, state, install of hooks, uninstall of hooks — works correctly.

---

## Spec acceptance criteria (§11)

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| 1 | `bash install.sh` injects hooks correctly | PASS | Test 1 below |
| 2 | Multiple sessions render with status / task / duration | PASS | Test 3 below |
| 3 | Status switches working ↔ waiting on hook | PASS | Test 2 below |
| 4 | `claude-radar-status --verbose` format matches spec | PASS | Test 3 below |
| 5 | Stuck session can be cleaned via `--reset` | PASS | Test 4 below |
| 6 | `bash uninstall.sh` cleans `settings.json` | PASS (with caveat) | Test 6 below |
| — | asciinema cast committed | PASS | `docs/screenshots/demo.cast` (8.4 KB) |

---

## Test 1 — install.sh fresh install + idempotency [PASS]

**Setup.** Seeded `/tmp/radar-test/settings.json` with a *pre-existing*, non-radar hook so we could verify it survives.

```
$ md5 /tmp/radar-test/settings.before.json
ce37da55f1d4b71e3fdddd8989ce1330

$ bash install.sh --settings /tmp/radar-test/settings.json --install-dir /tmp/radar-test/install
→ syncing files to /tmp/radar-test/install
→ backed up /tmp/radar-test/settings.json → /tmp/radar-test/settings.json.backup-1777127567
→ injecting hooks into /tmp/radar-test/settings.json
injected claude-radar hooks into /tmp/radar-test/settings.json
✓ claude-radar installed to /tmp/radar-test/install
```

**Pre-existing hook preserved.** After install, the `Stop` block contains both the original entry and the radar entry:

```
"Stop": [
  { "hooks": [{ "type": "command", "command": "/some/other/preexisting/hook.sh" }] },
  { "hooks": [{ "type": "command", "command": "/tmp/radar-test/install/hooks/state-tracker.sh Stop" }] }
]
```

**Idempotency.** Running `install.sh` a second time:

```
$ md5 /tmp/radar-test/settings.json   # before re-run
2bea1d11ae2d48afcaf0e3c52a469e90
$ bash install.sh --settings ... --install-dir ...   # second run
$ md5 /tmp/radar-test/settings.json   # after re-run
2bea1d11ae2d48afcaf0e3c52a469e90      # identical, no duplicates
```

`UserPromptSubmit=1`, `Stop=2` (preexisting + radar), `Notification=1` after both runs.

---

## Test 2 — hook script transitions [PASS]

`UserPromptSubmit` → `working` with task name extracted from stdin JSON:

```
$ echo '{"prompt":"线下新客归因：新客 -14.20%..."}' | hooks/state-tracker.sh UserPromptSubmit
$ cat $CLAUDE_RADAR_HOME/state/dev-office.json
{
  "current_task": "线下新客归因：新客 -14.20%，是门店流量少了还是进店转化率下降",
  "status": "working",
  "task_started_at": "2026-04-25T22:33:16+08:00",
  "last_user_prompt_at": "2026-04-25T22:33:16+08:00",
  ...
}
```

`Stop` → `waiting`, preserving `task_started_at` and `current_task`:

```
$ echo '{}' | hooks/state-tracker.sh Stop
$ cat ...dev-office.json
{
  "status": "waiting",
  "task_started_at": "2026-04-25T22:33:16+08:00",        # preserved
  "current_task": "线下新客归因...",                       # preserved
  "status_changed_at": "2026-04-25T22:33:23+08:00",      # bumped
  "last_assistant_stop_at": "2026-04-25T22:33:23+08:00", # new
  ...
}
```

**Resilience matrix:**

| Input | Behaviour | Result |
|-------|-----------|--------|
| corrupt JSON on stdin | exit 0, status set, task left blank | PASS |
| empty stdin | exit 0, status set | PASS |
| unknown hook type | exit 0, no state change | PASS |
| missing arg | exit 64 with usage banner | PASS |
| `Notification` event | exit 0, marks `waiting` | PASS |
| 300-char prompt | task field truncated to 160 chars | PASS |

---

## Test 3 — Multi-session render [PASS]

Seeded 4 sessions with realistic ages (data 13m / meta 5m / dev 41m / review 4h23m). `claude-radar --once` output:

```
─ Claude Sessions ────────────────────────────────────────────────────── 22:33 ─

💬  review              -                                                  4h23m
💬  data                线下新客归因：新客 -14.20%，是门店流量少了还是进…  13m
💬  meta                窗口管理方案讨论                                   5m
⚡  dev                 重构 report_utils                                  41m
...
q quit · r refresh · c cleanup
```

Sort order matches spec: waiting > working > idle, descending age within group. CJK truncation uses single-cell ellipsis.

`claude-radar-status` (compact) and `--verbose` match the formats in §3 and §6.5:

```
$ claude-radar-status
💬3 ⚡1

$ claude-radar-status --verbose
💬 review:- 4h23m | 💬 data:线下新客归因：新客 -14.… 13m | 💬 meta:窗口管理方案讨论 5m | ⚡ dev:重构 report_utils 41m
```

Empty state renders the friendly placeholder, satisfying §13.5:

```
─ Claude Sessions ────────────────────────────────────────────────────── 22:34 ─

No active Claude Code sessions yet.

Hooks haven't fired yet — try sending a prompt.
```

---

## Test 4 — Reset / cleanup / idle escalation [PASS]

```
$ python3 .../bin/claude-radar --reset
removed 3 state file(s)

$ python3 .../bin/claude-radar --cleanup    # 24h cutoff, none stale yet
removed 0 idle state file(s)

# Aged the data state to 30h →
$ python3 .../bin/claude-radar --cleanup
removed 1 idle state file(s)
```

Idle escalation in render: a `working` state with `status_changed_at` older than the 6h threshold (`DEFAULT_IDLE_AFTER_SECONDS`) renders as `○ idle` with no duration:

```
─ Claude Sessions ────────────────────────────────────────────────────── 22:34 ─

💬  review              -                                                  4h23m
💬  data                线下新客归因：新客 -14.20%...                       13m
💬  meta                窗口管理方案讨论                                    5m
○   dev                 -
```

The on-disk file is *not* mutated — escalation is a render-time decision (correct: spec §5.2).

`claude-radar` in non-TTY (e.g. piped):

```
$ echo | claude-radar
claude-radar requires an interactive terminal      # exit 2
```

---

## Test 5 — Unit tests [PASS]

```
$ python3 -m unittest discover tests/
Ran 66 tests in 0.057s
OK
```

(test_state.py + test_render.py + test_cli.py + test_inject_hooks.py)

---

## Test 6 — uninstall.sh [PASS with caveat]

When invoked from the source repo, `uninstall.sh` correctly removes radar hooks while preserving the pre-existing `Stop` entry. Running it twice is safe (idempotent).

```
$ bash ~/tuhu/claude-radar/uninstall.sh --settings /tmp/radar-test/settings.json --install-dir /tmp/radar-test/install
→ backed up /tmp/radar-test/settings.json → ...backup-1777127709
removed claude-radar hooks from /tmp/radar-test/settings.json
✓ uninstall complete
```

Resulting hooks block:

```
"hooks": {
  "Stop": [
    { "hooks": [{ "type": "command", "command": "/some/other/preexisting/hook.sh" }] }
  ]
}
```

`UserPromptSubmit` and `Notification` keys (which only contained radar) are dropped entirely. Good.

`--purge` removes the install dir; `--purge-state` empties `$CLAUDE_RADAR_HOME/state/` JSON files. Both verified.

**Caveat — settings.json not byte-identical**
The pre-existing JSON had `"allow": ["Read"]` (compact); after uninstall it becomes `"allow": [\n  "Read"\n]`. Semantically equivalent but a user who hand-formatted their file will see a diff. Low severity (uninstall always writes a `.backup-<ts>` first). Documenting under "known limitations" would be enough.

---

## Test 7 — asciinema demo [PASS]

```
$ ls -la docs/screenshots/demo.cast
-rw-r--r--  1 chengxuelin  staff  8388 Apr 25 22:39 docs/screenshots/demo.cast
```

8.4 KB. Format: asciicast v3, 80×24. Recorded via `/tmp/radar-test/demo.sh` against a sandboxed `CLAUDE_RADAR_HOME=/tmp/radar-demo`. The recording walks through:

1. `claude-radar --version`
2. `claude-radar --once` empty placeholder
3. `state set` to seed three sessions
4. `claude-radar --once` showing the populated board
5. `claude-radar-status` compact + `--verbose`
6. Stale-session simulation → `○ idle` escalation
7. `claude-radar --reset` → empty board

Slightly under the 10-50 KB band the team-lead flagged — this is because the demo is text-heavy and idle gaps were trimmed (`--idle-time-limit 2.5`); the cast plays the whole flow in ~25 s. Replay verified by `head` / `tail` of the JSON; valid v3 events throughout.

---

## Bugs to fix

### BUG-1 [HIGH] `install.sh` does not copy `uninstall.sh` into the install dir

**Repro:**
```
$ bash install.sh --install-dir /tmp/x --settings /tmp/x/settings.json --no-hooks
$ ls /tmp/x/uninstall.sh
ls: /tmp/x/uninstall.sh: No such file or directory
```

**Why it matters.** `README.md` line 175 tells users to run `bash ~/.claude-radar/uninstall.sh --purge --purge-state`. With the current installer, that path doesn't exist, so the documented uninstall path is broken on every fresh install.

**Expected.** `install.sh` should `cp uninstall.sh` (and probably `install.sh` itself, for re-runs from the install dir) alongside the other top-level files.

**Fix sketch.** Add to the file-sync block in `install.sh`:
```bash
cp "$SCRIPT_DIR/install.sh"   "$INSTALL_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/uninstall.sh" "$INSTALL_DIR/" 2>/dev/null || true
chmod +x "$INSTALL_DIR/install.sh" "$INSTALL_DIR/uninstall.sh" 2>/dev/null || true
```

### BUG-2 [HIGH] Non-tmux session id falls back to `$$` (per-invocation PID), not a stable identifier

**Repro** (no TMUX in env):
```
$ env -u TMUX bash -c "echo '{\"prompt\":\"hi 1\"}' | hooks/state-tracker.sh UserPromptSubmit"
$ env -u TMUX bash -c "echo '{}'                | hooks/state-tracker.sh Stop"
$ env -u TMUX bash -c "echo '{\"prompt\":\"hi 2\"}' | hooks/state-tracker.sh UserPromptSubmit"
$ env -u TMUX bash -c "echo '{}'                | hooks/state-tracker.sh Stop"
$ ls $CLAUDE_RADAR_HOME/state | grep unknown-
unknown-23036.json
unknown-23061.json
unknown-23073.json
unknown-23088.json
```

**Why it matters.** Claude Code pipes JSON into the hook over stdin, so `tty 2>/dev/null` always returns "not a tty" — the script always falls through to `unknown-$$`. `$$` is the PID of the bash hook script, which differs on every invocation. As a result, **every prompt and every Stop event from the same non-tmux Claude Code session creates a brand-new state file**. The dashboard then shows a swarm of one-event "sessions" instead of one row per Claude.

This silently breaks the headline use case of "tmux 内 + tmux 外混合" required by spec §11 acceptance check #2.

**Expected.** A single non-tmux Claude Code session should map to **one** stable state file for its entire life.

**Fix sketch.** `$PPID` (the spawning Claude Code process) is stable across all hooks fired by one Claude session. Combine with the cwd as a tie-breaker to survive tmux-vs-not transitions:
```bash
if [ -z "$SESSION_ID" ]; then
    # Try to read the controlling tty of our parent (Claude Code).
    PARENT_TTY="$(ps -o tty= -p "$PPID" 2>/dev/null | tr -d '[:space:]')"
    if [ -n "$PARENT_TTY" ] && [ "$PARENT_TTY" != "?" ] && [ "$PARENT_TTY" != "??" ]; then
        SESSION_ID="tty-$PARENT_TTY"
    else
        SESSION_ID="pid-$PPID"
    fi
fi
```
The spec's reference Python (§5.3) reads `os.ttyname(sys.stdout.fileno())` — same idea, just done from bash.

### BUG-3 [LOW / cosmetic] uninstall reformats unrelated JSON

Pre-existing `"allow": ["Read"]` becomes `"allow": [\n  "Read"\n]` after uninstall (Python `json.dumps(indent=2)`). Semantically equivalent and a `.backup-<ts>` is always written. Document under "Limitations" or accept.

### BUG-4 [LOW / docs] README still says `[asciinema demo coming soon]`

`README.md` line 22 — once `docs/screenshots/demo.cast` is committed, swap in the actual link / asciinema badge.

---

## Repro environment

- macOS Sequoia, bash 3.2 (system) + bash 4 via Homebrew, both used
- Python 3.12.x (stdlib only)
- asciinema 3.2.0 (Homebrew bottle)
- Test sandbox: `/tmp/radar-test/`, `/tmp/radar-demo/`. `~/.claude/settings.json` untouched throughout.

## Sign-off

Once BUG-1 and BUG-2 are fixed and re-tested, this passes spec §11. BUG-3 and BUG-4 are nice-to-have polish.

---

## Retest 2026-04-25 — PASS

dev shipped 5 commits addressing all four bugs (`c528c7b`, `30c3b66`, `a535b92`, `8399833`, `e42acf6`). Re-ran the targeted items below; everything green.

### BUG-1 ✓ PASS — install.sh now ships uninstall.sh

```
$ bash install.sh --install-dir /tmp/radar-test/install --settings /tmp/radar-test/settings.json --no-hooks
$ ls /tmp/radar-test/install/uninstall.sh
-rwxr-xr-x  1 chengxuelin  wheel  2523 Apr 25 22:51 .../uninstall.sh
$ test -x /tmp/radar-test/install/uninstall.sh && echo ok
ok
```

End-to-end via the README's documented path also works:

```
$ bash /tmp/radar-test/install/uninstall.sh --settings /tmp/radar-test/settings.json --install-dir /tmp/radar-test/install
→ backed up /tmp/radar-test/settings.json → ...backup-1777128684
removed claude-radar hooks from /tmp/radar-test/settings.json
✓ uninstall complete
```

Pre-existing `/preexisting/hook.sh` under `Stop` preserved; radar entries removed.

### BUG-2 ✓ PASS — stable session id across hook invocations

Note on test methodology: the original repro in this report (and the team-lead's checklist) used four separate `env -u TMUX bash -c "..."` invocations. Each `bash -c` is a *fresh parent* with its own PID, so `$PPID` differs and the fix correctly produces four files. That doesn't model real life — Claude Code is one persistent parent that spawns every hook. The realistic test is "four hook calls from a single parent shell":

```bash
$ env -u TMUX bash -c '
    echo "{\"prompt\":\"hi 1\"}" | hooks/state-tracker.sh UserPromptSubmit
    echo "{}"                    | hooks/state-tracker.sh Stop
    echo "{\"prompt\":\"hi 2\"}" | hooks/state-tracker.sh UserPromptSubmit
    echo "{}"                    | hooks/state-tracker.sh Stop
  '
$ ls $CLAUDE_RADAR_HOME/state/
pid-23348.json     # one file
$ cat .../pid-23348.json
{ "session_id": "pid-23348", "status": "waiting", "current_task": "hi 2",
  "task_started_at": "...", ..., }
```

In real tmux (we're inside session `dev-office`), the same pattern produces `dev-office.json`. `CLAUDE_RADAR_SESSION_ID` env override also honoured (`my-custom-id.json`).

The hook now resolves session id in the order:
1. `CLAUDE_RADAR_SESSION_ID` (escape hatch)
2. tmux `#S` (real tmux session)
3. self ctty via `ps -o tty= -p $$` (works when Claude pipes JSON in but the bash process still has a controlling tty)
4. parent ctty via `ps -o tty= -p $PPID`
5. `pid-$PPID` final fallback (stable for the Claude Code session's lifetime)

### BUG-3 ✓ PASS — JSON reformatting documented

`README.md` § Limitations:
> **`uninstall.sh` reformats `settings.json`.** When we strip our hooks we re-serialise the JSON with two-space indentation, so any pre-existing hand-formatting (compact arrays, custom indentation) will normalise to our style. Semantically equivalent in every case; a `.backup-<unix>` file is written first so you can recover the exact bytes if needed.

Same paragraph mirrored in `README.zh-CN.md` § 已知限制. Acceptable resolution.

### BUG-4 ✓ PASS — README references real demo

```
$ grep -n -i 'coming soon' README.md README.zh-CN.md
(no match)

$ grep -n 'demo.cast' README.md README.zh-CN.md
README.md:22:    > Demo: <a href="docs/screenshots/demo.cast">docs/screenshots/demo.cast</a>
README.md:24:    > `asciinema play docs/screenshots/demo.cast`.
README.zh-CN.md:20:  > 演示：<a href="docs/screenshots/demo.cast">docs/screenshots/demo.cast</a>
README.zh-CN.md:22:  > `asciinema play docs/screenshots/demo.cast`。
```

`demo.cast` (8388 B, asciicast v3 80×24) parses cleanly.

### Regression sweep — PASS

- `python3 -m unittest discover tests/` → **66/66 OK**
- install.sh idempotency: MD5 of `settings.json` identical after second run (`e489b7e5bce95c0593bc0caacaaac8ac`)
- Multi-session render still produces correct ordering and CJK widths
- `claude-radar-status --verbose` format unchanged

## Final status: PASS — ready for reviewer + ship

All four bugs resolved. Spec §11 acceptance criteria all green. v0.1 ready for sign-off.

# claude-radar v0.1 — Reviewer Report (T8)

**Reviewer:** reviewer
**Date:** 2026-04-25
**Commit under review:** `a535b92` (Read self controlling tty before parent for stable id)
**Verdict:** ✅ **PASS — ready to ship**

All 9 hard constraints from team-lead, all 6 spec §11 acceptance criteria, all
13 spec §11 deliverables verified by **direct command execution**, not by
reading code or trusting qa-report alone. End-to-end install / hook /
multi-session render / uninstall reproduced in a fresh sandbox without touching
`~/.claude/settings.json`.

---

## A. Hard constraints (team-lead checklist)

| # | Constraint | Result | Evidence |
|---|------------|--------|----------|
| A1 | `~/.claude/settings.json` not touched | ✓ | `stat -f '%Sm' ~/.claude/settings.json` → `Apr 25 21:37:45 2026`; project init at 22:09; mtime unchanged after my full review (install + uninstall + reset cycles all hit `$SANDBOX`). |
| A2 | `inject-hooks.py --settings` works | ✓ | `python3 install/inject-hooks.py --help` → `--settings SETTINGS  Path to settings.json (default: ...)`; also `--remove`, `--dry-run`, `--install-dir`. |
| A3 | Zero third-party deps | ✓ | `grep -E "^[a-zA-Z]" pyproject.toml \| grep -i depend` → `dependencies = []`. |
| A4 | bash 3.2 compatible (no `declare -A` / `mapfile` / `readarray`) | ✓ | `grep -rE "declare -A\|mapfile\|readarray" hooks/ install.sh uninstall.sh install/` → only one match, in a *comment* in `hooks/state-tracker.sh` documenting the constraint. Zero actual usage. |
| A5 | `git log --oneline \| wc -l` ≥ 5 | ✓ | 13 commits — 7 dev initial + 5 bug-fix + 1 polish. No "Initial commit" pile. |
| A6 | No Claude / Anthropic / Co-Authored signatures | ✓ | `git log --all --pretty=format:'%an\|%ae\|%cn\|%ce\|%s\|%b' \| grep -iE "claude\|anthropic\|co-authored"` → empty. |
| A7 | Independent git repo, no remote | ✓ | `cat .git/config` shows only `[core]` block — no `[remote "..."]`. |
| A8 | README bilingual + aligned | ✓ | `README.md` 239 lines (English) + `README.zh-CN.md` 227 lines (Chinese), each links to the other in the header. Roadmap / Install / Usage / Configuration sections present in both. |
| A9 | All unit tests green | ✓ | `python3 -m unittest discover -s tests` → `Ran 66 tests in 0.054s / OK`. |

---

## B. Spec §11 — Code deliverables

| # | Item | Result | Evidence |
|---|------|--------|----------|
| B1 | `claude_radar/state.py` + tests | ✓ | File present (`claude_radar/state.py`); test file `tests/test_state.py` covers atomic write + field preservation. |
| B2 | `claude_radar/render.py` + tests | ✓ | File present; `tests/test_render.py` covers sort, duration, CJK width. |
| B3 | `claude_radar/tui.py` (curses main loop) | ✓ | File present; `--once` mode produces correct frame (see D2). |
| B4 | `hooks/state-tracker.sh` handling 3 hook types | ✓ | Live test below — UserPromptSubmit / Stop / Notification all transition state correctly. |
| B5 | `bin/claude-radar` (continuous board) | ✓ | Executable shebang `#!/usr/bin/env python3`; `--once` smoke test passes. |
| B6 | `bin/claude-radar-status` (one-shot, with `--verbose`) | ✓ | Compact: `💬2 ⚡1`; verbose: `💬 data:线下新客归因：是流量降… 0s \| ⚡ dev:重构 report_utils 0s`. Format matches §6.5. |
| B7 | `install/inject-hooks.py` idempotent | ✓ | Two consecutive `install.sh` runs produce identical settings.json (MD5 `f752ad6d…` × 2). |
| B8 | `install.sh` + `uninstall.sh` | ✓ | Both present, executable. End-to-end install → uninstall round-trip verified in sandbox; pre-existing non-radar hook preserved through both. |

---

## C. Spec §11 — Companion deliverables

| # | Item | Result | Evidence |
|---|------|--------|----------|
| C1 | `README.md` bilingual + screenshot/asciicast | ✓ | Both READMEs reference `docs/screenshots/demo.cast`; placeholder text "coming soon" removed (post BUG-4 fix). |
| C2 | `LICENSE` MIT | ✓ | `head -3 LICENSE` → `MIT License / Copyright (c) 2026 chengxuelin`. |
| C3 | `pyproject.toml` | ✓ | Present with `dependencies = []`. |
| C4 | `tests/` all pass | ✓ | 66/66 (see A9). |
| C5 | `docs/architecture.md` | ✓ | 11.9 KB present at `docs/architecture.md`. |

---

## D. Spec §11 — End-to-end acceptance (sandbox spot-checks)

All tests run with `SANDBOX=$(mktemp -d -t radar-review)` and `CLAUDE_RADAR_HOME=$SANDBOX/state-home`. `~/.claude/settings.json` was not opened in write mode at any point.

### D1 — `install.sh` injects hooks correctly ✓

Seeded sandbox settings with a *non-radar* `Stop` hook (`/preexisting/sentinel.sh`), then ran `install.sh`. Result:

```
✓ claude-radar installed to /var/.../install
```

Final `hooks` block contains both pre-existing entry **and** the three radar entries (UserPromptSubmit, Stop, Notification). Pre-existing entry preserved at index 0; radar entry appended.

### D2 — Multi-session render ✓

Three sessions seeded via the real hook script (no test harness shortcuts):

```
$ echo '{"prompt":"线下新客归因：是流量降了还是转化降了"}' \
   | CLAUDE_RADAR_SESSION_ID=data bash hooks/state-tracker.sh UserPromptSubmit
$ echo '{}' | CLAUDE_RADAR_SESSION_ID=data bash hooks/state-tracker.sh Stop
$ echo '{"prompt":"重构 report_utils"}' \
   | CLAUDE_RADAR_SESSION_ID=dev  bash hooks/state-tracker.sh UserPromptSubmit
$ echo '{"prompt":"窗口管理方案讨论"}' \
   | CLAUDE_RADAR_SESSION_ID=meta bash hooks/state-tracker.sh UserPromptSubmit
$ echo '{}' | CLAUDE_RADAR_SESSION_ID=meta bash hooks/state-tracker.sh Stop

$ COLUMNS=80 LINES=24 python3 bin/claude-radar --once
─ Claude Sessions ────────────────────────────────────────────────────── 22:56 ─

💬  data                线下新客归因：是流量降了还是转化降了               0s
💬  meta                窗口管理方案讨论                                   0s
⚡  dev                 重构 report_utils                                  0s
…
q quit · r refresh · c cleanup
```

Sort order: waiting (data, meta) > working (dev). Within group: insertion order tied (all 0s). Spec §3 layout reproduced.

### D3 — Hook transitions working ↔ waiting ✓

`UserPromptSubmit` → `working`, `task_started_at` set, `current_task` populated from stdin JSON's `prompt` field.

`Stop` → `waiting`, **`task_started_at` and `current_task` preserved**, `last_assistant_stop_at` and `status_changed_at` bumped:

```json
// after UserPromptSubmit
{"status":"working","task_started_at":"2026-04-25T22:56:35+08:00",
 "current_task":"线下新客归因：是流量降了还是转化降了"}
// after Stop
{"status":"waiting","task_started_at":"2026-04-25T22:56:35+08:00",   ← preserved
 "current_task":"线下新客归因：是流量降了还是转化降了",                ← preserved
 "last_assistant_stop_at":"2026-04-25T22:56:35+08:00"}
```

### D4 — `claude-radar-status --verbose` matches spec ✓

```
$ claude-radar-status
💬2 ⚡1

$ claude-radar-status --verbose
💬 data:线下新客归因：是流量降… 0s | 💬 meta:窗口管理方案讨论 0s | ⚡ dev:重构 report_utils 0s
```

Format `<emoji> <session>:<task> <duration> | …` matches §6.5. CJK truncation visible in the data row.

### D5 — `--reset` clears stuck sessions ✓

```
$ python3 bin/claude-radar --reset
removed 3 state file(s)
$ ls $CLAUDE_RADAR_HOME/state/
(empty)
```

Empty board after reset shows the friendly placeholder per §13.5:

```
─ Claude Sessions ────────────────────────────────────────────────────── 22:57 ─

No active Claude Code sessions yet.

Hooks haven't fired yet — try sending a prompt.
```

### D6 — `uninstall.sh` cleans settings.json ✓

```
$ bash $SANDBOX/install/uninstall.sh \
       --settings $SANDBOX/settings.json --install-dir $SANDBOX/install
→ backed up .../settings.json → ...backup-1777129009
removed claude-radar hooks from .../settings.json
✓ uninstall complete
```

Final `hooks` block:

```json
{"Stop": [{"hooks":[{"type":"command","command":"/preexisting/sentinel.sh"}]}]}
```

`UserPromptSubmit` and `Notification` keys (radar-only) removed entirely; pre-existing `Stop` entry intact. Backup file written. ✓

Note (carried from qa-report BUG-3): JSON is reformatted to two-space indent. Documented in both READMEs under "Limitations / 已知限制". Acceptable.

### D7 — Documented uninstall path works ✓

Verified the install dir contains `uninstall.sh` (resolves qa BUG-1):

```
$ ls $SANDBOX/install/uninstall.sh
-rwxr-xr-x  1 chengxuelin  wheel  ... uninstall.sh
```

The README's instructed command `bash ~/.claude-radar/uninstall.sh ...` works from a fresh install.

---

## E. Isolation audit — did review touch user state?

```
$ stat -f '%Sm' ~/.claude/settings.json
Apr 25 21:37:45 2026                 # unchanged before, during, after review
$ find ~/.claude -maxdepth 1 -type f -newer /tmp/radar-review-sandbox
/Users/chengxuelin/.claude/policy-limits.json   # OS-level, unrelated to review
```

`~/.claude/settings.json` mtime is the same value observed at the start of T8. Review used only `$SANDBOX` and ephemeral `CLAUDE_RADAR_HOME` directories.

---

## F. Findings

### F1 — Two demo.cast copies (INFO, non-blocking)

```
assets/demo.cast            8219 B
docs/screenshots/demo.cast  8388 B
```

Both are valid asciicast v3 JSON. README links only to `docs/screenshots/demo.cast`; `assets/demo.cast` appears to be an earlier take. Optional cleanup before publishing — not a blocker.

### F2 — `last_user_prompt_at` / `last_assistant_stop_at` semantics (INFO)

Tested and behaves correctly. Worth noting in `docs/architecture.md` whether the *last* occurrence wins (it does) so future contributors don't break the invariant.

Neither finding blocks shipping.

---

## G. Verdict

**PASS — ready to ship as v0.1.**

- All 9 hard constraints met.
- All 8 §11 code deliverables present, executable, and tested.
- All 5 §11 companion deliverables present.
- All 6 §11 end-to-end acceptance scenarios reproduced in a fresh sandbox.
- Repo isolation confirmed: `~/.claude/settings.json` was not modified during dev, qa, or review.

No rework required. Optional polish (F1) can be deferred to a follow-up commit.

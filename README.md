<p align="left">
  <img src="docs/twmux-logo.png" alt="twmux logo" width="300" />
</p>

Race-condition-safe **tmux** wrapper built for LLM coding agents — but equally pleasant for humans. Also ships with `twmux watch`: a small daemon + dashboard that surfaces every running coding agent across your tmux sessions, sorted by how long each one has been waiting on you.

## Why twmux?

LLM agents need to run shell commands, but raw tmux is fragile:
Enter keys get lost, output parsing breaks, errors produce tracebacks instead of structured data.
twmux solves this with race-condition-safe I/O, a consistent JSON contract, and agent-safe socket isolation.

**For agents:** Every command returns `{"ok": true, ...}` or `{"ok": false, "error": "..."}`. No tracebacks. No Rich markup in JSON. Predictable exit codes. Self-discoverable via `twmux --json`.

**For humans:** Rich-formatted output, helpful error messages, monitor commands for watching agent work. The `--json` flag is opt-in; without it, everything is human-friendly.

## Features

- **Structured JSON contract** - Consistent `{"ok": true/false, ...}` envelope for all commands, errors included
- **Agent isolation** - Default socket `claude` keeps agent operations separate from user tmux
- **Safety boundaries** - Non-agent sockets require `--force` flag
- **Race-condition-safe send** - Verifies commands are received before sending Enter; text is typed literally, multi-line text arrives as one bracketed paste
- **Execute and capture** - Run commands and get output with exit codes
- **Marker-based execution** - Reliable output capture using unique markers
- **Wait-idle detection** - Wait until pane output stabilizes
- **Self-discoverable** - `twmux --json` lists all commands; `twmux --json status` exposes all targets
- **Flexible targeting** - Pane IDs or session:window.pane syntax
- **Pane management** - Launch, kill, interrupt, move, and escape
- **Cross-session moves** - Move panes and windows between sessions
- **Zero tracebacks** - All errors are caught and formatted, even connection failures
- **Agent monitor** - [`twmux watch`](#twmux-watch--multi-agent-dashboard--switcher): which of your agents waits, finished unseen, or works — in a popup, a jump key, and the tmux status bar; agents can report their own state via hooks (`twmux mark`)

Nothing you couldn't do with bare tmux, but much more reliable for agent use.

## Agent Isolation

By default, twmux operates on the `claude` socket, keeping agent tmux sessions separate from your personal tmux:

```bash
# Agent operations (default socket: claude)
twmux new myapp
twmux send -t %0 "echo hello"
twmux status

# User can monitor without interference
tmux -L claude attach -t myapp   # Watch agent work
# Ctrl+b d to detach

# Access user's tmux requires explicit --force
twmux --force -L default status  # View user's default socket
```

Socket naming:
- `claude`, `claude-*` - Agent sockets (no `--force` needed)
- All other names - Require `--force` flag

## Installation

```bash
uv pip install -e .
```

## Usage

```bash
twmux [OPTIONS] COMMAND [ARGS]
```

### Global Options

| Option | Description |
|--------|-------------|
| `--json` | Output as JSON (for programmatic use) |
| `-L, --socket NAME` | tmux socket name (default: `claude`) |
| `--force` | Allow non-agent sockets (required for non-`claude*` sockets) |
| `-v, --verbose` | Verbose output |

## Commands

### send - Send text safely

Send text to a pane with race-condition-safe Enter handling.

```bash
twmux send -t %5 "echo hello"
twmux send -t main:0.1 "make test" --delay 0.1
twmux send -t %5 "partial text" --no-enter
```

### exec - Execute and capture

Execute a command and capture output with exit code.

```bash
twmux exec -t %5 "ls -la"
twmux --json exec -t main:0 "make test" --timeout 60
```

Returns:
- `output`: Command stdout/stderr
- `exit_code`: Command exit code (-1 if timeout)
- `timed_out`: Whether command timed out

### capture - Capture pane content

```bash
twmux capture -t %5
twmux capture -t %5 -n 50  # Last 50 lines
twmux --json capture -t main:0
```

### wait-idle - Wait for output stabilization

Wait until pane output stops changing.

```bash
twmux wait-idle -t %5
twmux wait-idle -t %5 --timeout 10 --interval 0.1
```

### interrupt - Send Ctrl+C

```bash
twmux interrupt -t %5
```

### escape - Send Escape key

```bash
twmux escape -t %5
```

### launch - Create new pane

Split current pane to create a new one.

```bash
twmux launch -t %5                           # Split below
twmux launch -t %5 -v                        # Split right (vertical)
twmux launch -t %5 -c "python3"              # Split; type command into shell
twmux launch -t %5 --exec -c "nvim /tmp/x"   # Command IS pane process; pane dies on exit
twmux launch -t %5 --focus -c "python3"      # Split and move cursor to new pane
```

By default focus stays on the original pane (matches libtmux's detached split).
Use `--focus` to make the new pane active immediately.

With `--exec`, the command replaces the shell as the pane's PID 1. The pane
terminates automatically when the command exits — pair with `wait-pane` to
block until an editor or TUI is closed.

### kill - Kill pane

```bash
twmux kill -t %5
```

### wait-pane - Block until pane is gone

Polls the tmux server until the target pane no longer exists. Idempotent —
returns immediately if the pane is already gone.

```bash
twmux wait-pane -t %5                        # Wait forever
twmux wait-pane -t %5 --timeout 60           # Error after 60s if still alive
twmux wait-pane -t %5 --interval 0.1         # Poll every 100ms
```

Returns: `{"ok": true, "gone": true, "elapsed": 1.23}`. Exits 1 with
`{"ok": false, "error": "timeout after Ns"}` on timeout.

### move-pane - Move pane to another session

Move a pane to another session, creating a new window or joining an existing one.

```bash
twmux move-pane -t %5 debug           # New window in "debug"
twmux move-pane -t %5 debug:0         # Join window 0 in "debug"
twmux --json move-pane -t %5 debug    # JSON output
```

Returns: `{"ok": true, "pane_id": "%5", "destination_session": "debug", "new_window": true}`

### move-window - Move window to another session

Move an entire window (with all panes) to another session.

```bash
twmux move-window -t build:0 debug       # Move window 0 of "build"
twmux move-window -t %5 debug            # Move window containing %5
twmux --json move-window -t build:0 debug # JSON output
```

Returns: `{"ok": true, "window_id": "@1", "window_index": "1", "pane_ids": ["%2", "%3"], "destination_session": "debug"}`

### new - Create session

Create a new tmux session on the agent socket. Prints monitor command for user observation.

```bash
twmux new myapp                    # Create session "myapp"
twmux new myapp -c "python3"       # Create and run command
twmux -L claude-isolated new test  # Use different agent socket
```

Output includes monitor command:
```
Session created: myapp on socket claude
Pane ID: %0

To monitor:  tmux -L claude attach -t myapp
To detach:   Ctrl+b d
```

### kill-session - Kill session

```bash
twmux kill-session myapp
```

### kill-server - Kill server

Kill the entire tmux server for a socket.

```bash
twmux kill-server                    # Kill default claude server
twmux -L claude-isolated kill-server # Kill specific socket
```

### status - Show tmux state

```bash
twmux status                # Show default socket (claude)
twmux status --all          # Show all agent sockets (claude*)
twmux --force status --all  # Show all sockets including user's
```

### reattach - Restore TMUX env in a scrubbed shell

After `exec env -i ... bash --login` inside a tmux pane, `TMUX` and `TMUX_PANE`
are gone — but the shell is still attached to the same pane's tty. `reattach`
finds that pane on the given socket (by matching its tty) and prints the
`export` lines needed to rebuild the env.

```bash
# Typical recovery: human shell on the default socket
eval "$(twmux -L default --force reattach)"

# JSON envelope (e.g. for an agent)
twmux -L default --force reattach --json

# --tty is normally unneeded (defaults to /dev/tty). Use $(tty) if you must:
twmux -L default --force reattach --tty "$(tty)"
```

Socket selection uses the global `-L`/`--socket` flag like every other
command. The default socket is `claude`; targeting `default` (or any
non-`claude*` socket) requires `--force`, same as elsewhere.

## Target Addressing

The `-t` option accepts tmux target syntax to identify panes.

### Pane ID (Recommended)

Direct pane reference using tmux pane ID:

```bash
twmux send -t %5 "echo hello"      # Pane ID %5
twmux exec -t %12 "ls"             # Pane ID %12
```

Get pane IDs with `twmux status` or `tmux list-panes -a`.

### Session:Window.Pane Format

Hierarchical addressing:

```bash
# Full path: session:window.pane
twmux send -t main:0.1 "echo hello"    # Session "main", window 0, pane 1
twmux send -t dev:2.0 "make test"      # Session "dev", window 2, pane 0

# Partial paths
twmux send -t main:0 "echo hello"      # Session "main", window 0, active pane
twmux send -t main: "echo hello"       # Session "main", active window/pane
twmux send -t :0.1 "echo hello"        # First session, window 0, pane 1
```

### Target Resolution

| Target | Meaning |
|--------|---------|
| `%5` | Pane with ID %5 (absolute) |
| `main:0.1` | Session "main", window 0, pane 1 |
| `main:0` | Session "main", window 0, active pane |
| `main:` | Session "main", active window and pane |
| `:0.1` | First session, window 0, pane 1 |
| `:0` | First session, window 0, active pane |
| (empty) | First session, active window and pane |

### Examples

```bash
# Start a REPL in a new pane and interact with it
twmux launch -t %5 -c "python3"
# Returns: {"ok": true, "pane_id": "%12"}

# Send commands to the new pane
twmux send -t %12 "print('hello')"
twmux wait-idle -t %12

# Capture output
twmux capture -t %12 -n 10

# Execute and get result
twmux --json exec -t %12 "print(1+1)"
# Returns: {"ok": true, "output": "2", "exit_code": 0, "timed_out": false}

# Clean up
twmux kill -t %12
```

## JSON Output

All commands support `--json` for programmatic use. Every response follows a consistent envelope:

```
Success: {"ok": true,  ...command-specific-fields}
Error:   {"ok": false, "error": "human-readable message"}
```

Exit codes: `0` = success, `1` = any error. All JSON goes to stdout.

### Examples

```bash
$ twmux --json exec -t %5 "echo hello"
{"ok": true, "output": "hello", "exit_code": 0, "timed_out": false}

$ twmux --json send -t %5 "test"
{"ok": true, "success": true, "attempts": 1}

$ twmux --json send -t %999 "test"
{"ok": false, "error": "Pane not found: %999"}

$ twmux --json new myapp
{"ok": true, "session": "myapp", "socket": "claude", "pane_id": "%0", "monitor_cmd": "tmux -L claude attach -t myapp"}

$ twmux --json status
{
  "ok": true,
  "sockets": [
    {
      "socket": "claude",
      "sessions": [
        {
          "session_id": "$0",
          "session_name": "myapp",
          "windows": [...]
        }
      ]
    }
  ]
}
```

### Agent Self-Discovery

An agent encountering twmux for the first time can bootstrap itself:

```bash
# Discover available commands
$ twmux --json
{"ok": true, "commands": [{"name": "send", "description": "..."}, ...]}

# Discover available targets
$ twmux --json status
{"ok": true, "sockets": [{"sessions": [{"session_name": "myapp", "windows": [{"panes": [{"pane_id": "%0"}]}]}]}]}

# Use discovered target
$ twmux --json send -t %0 "echo hello"
{"ok": true, "success": true, "attempts": 1}
```

### Agent Integration Pattern

```python
import json, subprocess

def twmux(cmd: list[str]) -> dict:
    result = subprocess.run(
        ["twmux", "--json"] + cmd,
        capture_output=True, text=True,
    )
    response = json.loads(result.stdout)
    if not response["ok"]:
        raise RuntimeError(response["error"])
    return response

# One parser for all commands
twmux(["send", "-t", "%0", "make test"])
twmux(["wait-idle", "-t", "%0"])
output = twmux(["capture", "-t", "%0"])["content"]
```

## How It Works

### Race-Condition-Safe Send

The `send` command:
1. Sends text without Enter — literally (`"C-c"` is typed, not pressed);
   multi-line text as one bracketed paste, so embedded newlines never submit
   early (an agent TUI receives the whole prompt; Enter submits it)
2. Waits (configurable delay)
3. Captures pane content
4. Sends Enter
5. Verifies content changed
6. Retries if needed
7. If still unchanged, keeps watching (up to 3s, no further Enters) before
   reporting failure — a still-starting shell runs the queued line late, and a
   false failure would tempt the caller to send the command twice

### Marker-Based Execution

The `exec` command:
1. Generates unique markers
2. Wraps command: `echo START; { cmd; } 2>&1; echo END:$?`
3. Polls pane with progressive expansion (100 → 500 → 2000 → all lines)
4. Parses output between markers
5. Extracts exit code

### Output Stabilization

The `wait-idle` command:
1. Hashes pane content (MD5)
2. Polls at configurable interval
3. Returns when N consecutive hashes match
4. Times out if content keeps changing

## Python Library

twmux's value-added primitives — race-safe send, marker-based execution, and
idle detection — are importable directly from `twmux.lib.*`. Pair them with
`libtmux` for pane/session management; there is no separate wrapper API to
learn.

```python
import libtmux
from twmux.lib.safe_input import send_safe, wait_for_idle
from twmux.lib.execution import execute
from twmux.lib.safety import validate_socket

# Optional: enforce agent-socket policy (raises SocketValidationError)
validate_socket("claude", force=False)

server = libtmux.Server(socket_name="claude")
pane = server.sessions[0].active_window.active_pane

# Race-safe send: verifies pane content changed after Enter, retries on loss
result = send_safe(pane, "make test", enter=True)
assert result.success, f"lost Enter after {result.attempts} attempts"

# Marker-based execute: captures output + real exit code
res = execute(pane, "echo hello && false", timeout=10)
print(res.output)      # "hello"
print(res.exit_code)   # 1
print(res.timed_out)   # False

# Wait until the pane stops changing
wait_result = wait_for_idle(pane, poll_interval=0.2, stable_count=3, timeout=30)
```

### Public surface

| Module | Exports |
|--------|---------|
| `twmux.lib.safe_input` | `send_safe(pane, text, enter=True, enter_delay=0.05, settle_timeout=3.0) -> SendResult`, `wait_for_idle(pane, poll_interval=0.2, stable_count=3, timeout=30.0) -> WaitResult` |
| `twmux.lib.execution` | `execute(pane, cmd, timeout=30.0, poll_interval=0.2) -> ExecResult`, `ExecResult(output, exit_code, timed_out)` |
| `twmux.lib.safety` | `validate_socket(socket_name, force)`, `is_agent_socket(socket_name)`, `enumerate_agent_sockets()`, `SocketValidationError` |

### What about launch / kill / status / move-pane?

These are thin wrappers over libtmux — call libtmux directly:

```python
# Instead of `twmux launch`:
new_pane = pane.split(shell="python3")          # --exec equivalent
new_pane = pane.split()                          # plain split
new_pane.select()                                # --focus equivalent

# Instead of `twmux kill`, `twmux status`:
pane.kill()
[p.pane_id for p in server.panes]
```

The CLI exists for the JSON envelope and agent-subprocess orchestration — if
you're already in Python, libtmux is the API for those operations.

## `twmux watch` — multi-agent dashboard & switcher

When you run several coding agents in parallel — across multiple tmux sessions,
windows, and panes — it's easy to lose track of which ones are blocked on a
question, which are still working, and which finished while you were looking
elsewhere. `twmux watch` is one small daemon that tracks every agent pane and
tells you, in four places, which ones need you.

### At a glance

```
 agent hooks ──► twmux mark ──► tmux pane options ─┐
 (Claude Code, Copilot, …)      @twmux_state       │
                                                   ▼
 every pane on every socket ──────────────► twmux watch daemon  (poll every 2s)
 (title, command, screen content)            classify + track per pane
                                                   │
        ┌──────────────────────┬──────────────────┼─────────────────────┐
        ▼                      ▼                  ▼                     ▼
 agents.tsv              agents.json        @twmux_status          @twmux_summary
 prefix a: fzf popup     prefix A:          window option:         global option:
 (all agents, sorted)    twmux watch next   icon in window list    "💬1 ✅2" in
                         (jump, no popup)   (current session)      status-right
                                                                   (all sessions)
```

| You want to… | Use | Shows |
| --- | --- | --- |
| See every agent and its state | `prefix a` (switcher popup) | All agents on all sockets, most urgent first, live preview |
| Go straight to whoever needs you | `prefix A` (`twmux watch next`) | Jumps to the newest `wait`/`done` agent; press again for the next older one |
| Know which window needs you | Status bar window list | `💬` / `✅` / `🤖` after the window name — **current session only** |
| Know that *anything* needs you | Status bar right side | `💬1 ✅2` — counts across **all sessions** of this tmux server |

### Agent states

| State | Meaning | Ends when |
| --- | --- | --- |
| `wait` 💬 | Agent is blocked on you: permission prompt or a question | You answer |
| `done` ✅ | Agent finished **while its window was not in front of you** | A poll finds its window in front of you → `idle` |
| `working` 🤖 | Agent is processing | It finishes, asks, or is interrupted |
| `idle` | Agent is at its prompt and you have seen it | You give it a new task |
| `unknown` | Matched as an agent, but nothing tells its state | — |

```
             prompt submitted                  question / permission prompt
   idle ─────────────────────► working ─────────────────────────────► wait
    ▲                            │  ▲                                  │
    │ window seen                │  └──────────── answered ────────────┘
    │                            │ finished
   done ◄── window not seen ─────┤
                                 └── window seen ──► idle
```

"In front of you" means: the pane's window is the active window of a session
that has a client attached. The daemon checks this at each poll, so a visit
shorter than `poll_interval` (2s) does not count as seen. Agents found when the
daemon starts count as seen — a daemon restart never floods the list with
`done`, and it resets all time-in-state counters.

### Quick start

```bash
# 1. Install twmux and seed ~/.config/twmux/agents.toml (opens $EDITOR)
uv tool install --upgrade twmux
twmux config

# 2. Install the switcher popup script
cp examples/tmux-agent-switcher ~/.config/tmux/
chmod +x ~/.config/tmux/tmux-agent-switcher
```

3. Add to `~/.config/tmux/tmux.conf`, then reload (`tmux source-file …`):

```tmux
# Start the daemon with tmux (or use launchd, see "Daemon lifecycle")
run-shell -b 'twmux watch daemon --ensure-running'

# Switcher popup: all agents, Enter jumps
bind-key a display-popup -h 80% -w 90% -E "~/.config/tmux/tmux-agent-switcher"
# Jump to the newest waiting/done agent, no popup; press again to cycle.
# (Not `n`: that is tmux's next-window.)
bind-key A run-shell "twmux watch next"

# Window icon: append to your existing window formats
setw -g window-status-format         '#I:#W#{?@twmux_status, #{@twmux_status},} '
setw -g window-status-current-format '#I:#W#{?@twmux_status, #{@twmux_status},} '

# Summary across all sessions: prepend to your existing status-right.
# Commas inside #{?…} branches must be escaped as "#,".
set -g status-right '#{?@twmux_summary,#[fg=#ffb86c#,bold]#{@twmux_summary}#[default] | ,}%a %d %b %R'
```

4. **Recommended:** install agent hooks (see "Agent hooks" below) so agents
   report their state instead of the daemon guessing it from the screen.
5. **Restart running agent sessions** — agents read their hook config only at
   session start (`claude --continue`, `copilot --resume` keep the
   conversation).

Without step 4 everything still works, from screen-scraping alone.

### How the state is determined

Each poll, per agent pane, the daemon combines up to two sources:

| Source | How | Strength | Weakness |
| --- | --- | --- | --- |
| **Regexes** (`agents.toml`) | `re_wait` / `re_working` / `re_idle` on the captured screen | Works for any agent, zero setup | Breaks when the agent's TUI changes; can flap between redraws |
| **Agent hooks** (`twmux mark`) | The agent calls `twmux mark <state>` on its lifecycle events | Exact: the agent says what it does | Needs hook config; hooks miss some events (Ctrl-C) |

#### Which panes are agents

A pane is an agent if **either**:

- an `agents.toml` section matches it — `cmd_match` on the pane's current
  command **or** `title_match` on its title (first section wins; an optional
  `content_match` must then also match the screen), **or**
- it carries a valid hook mark (`@twmux_state`) — even if no regex recognises
  it. The `--agent` name from the mark is shown.

#### Agent hooks — `twmux mark`

```bash
twmux mark working|wait|done|clear [--agent NAME]
```

Run by an agent's hooks, inside the agent's pane. It finds that pane from the
`$TMUX` / `$TMUX_PANE` the hook inherits, and writes **tmux pane options**:

| Option | Content |
| --- | --- |
| `@twmux_state` | `working` \| `wait` \| `done` |
| `@twmux_state_ts` | Epoch seconds of the mark — a new mark resets staleness tracking |
| `@twmux_cmd` | The pane's current command at mark time (e.g. `2.1.323` for Claude Code) |
| `@twmux_agent` | The `--agent` name |

`clear` removes all four. Pane options live and die with the pane, so there are
no state files and nothing to clean up. Inspect a pane's marks with
`tmux show-options -p -t <pane> | grep twmux`.

`mark` is built to be safe inside hooks:

- **Silent** in human mode — Claude Code feeds some hooks' stdout to the model.
- **No-op, exit 0** outside tmux, with `TWMUX_NO_MARK=1` (set it on nested
  agents launched from an agent pane, so only the outer agent drives the
  pane), and inside Claude background jobs (`CLAUDE_JOB_DIR` set).
- Takes ~0.1s.

Ready-made hook configs:

| Agent | File | Install as | Status |
| --- | --- | --- | --- |
| Claude Code | `examples/hooks/claude-code-settings.json` | merge `hooks` into `~/.claude/settings.json` | in daily use |
| Copilot CLI | `examples/hooks/copilot-hooks.json` | copy to `~/.copilot/hooks/twmux.json` | 1.0.110: `working`/`done` verified; `notification` → `wait` matcher not yet verified |
| Codex | `examples/hooks/codex-hooks.json` | merge into `~/.codex/hooks.json` (needs `[features] hooks = true`) | untested |

Event mapping (Claude Code names; the others are equivalent):

| Event | Mark |
| --- | --- |
| `SessionStart`, `SessionEnd` | `clear` |
| `UserPromptSubmit`, `PostToolUse` (no matcher — every tool) | `working` |
| `Notification` matcher `permission_prompt\|elicitation_dialog` | `wait` |
| `Stop` | `done` |

`PostToolUse` must run for **every** tool, not just `Bash`: it is what flips
`wait` back to `working` after you approve a prompt for, say, an Edit.

> **Never keep these hooks with a twmux that lacks `mark`.** An unknown
> subcommand exits with code 2, which Claude Code treats as *blocking*:
> `UserPromptSubmit` would reject every prompt and `Stop` would keep the agent
> from finishing.

Agents in a sandbox: `mark` must reach the tmux socket. With `srt`, allow Unix
sockets under the tmux socket directory (e.g. `/private/tmp` on macOS) in
`~/.srt-settings.json` → `network.allowUnixSockets`.

#### Combining hook and screen

The hook is trusted first, the screen cross-checks it:

| Situation | Result | Why |
| --- | --- | --- |
| Screen shows a wait prompt (`re_wait`) | `wait`, whatever the hook says | Some prompts fire no hook |
| No valid mark | Regex state | Agent without hooks |
| Mark made under another command than the pane runs now | Mark ignored | The agent exited (e.g. `2.1.323` → `bash`) |
| Mark `working`, screen frozen for `hook_stale_secs` (10s) | Mark stale → regex state | Ctrl-C/Escape fire no hook; a working agent redraws every second |
| Mark `wait`/`done`, screen changed *after* the mark, then frozen 10s | Mark stale → regex state | Something happened that no hook reported (e.g. prompt rejected) |
| Mark `wait`/`done`, screen unchanged | Mark holds | These screens are static by nature |
| Mark `done` | Treated as `idle` → shown as `done` until seen | See "Agent states" |

A new mark (new `@twmux_state_ts`) is trusted again.

### What you see

#### Switcher popup — `prefix a`

An fzf list of all agents on all sockets, sorted by urgency (`wait` → `done` →
`idle` → `working` → `unknown`; within a state, most recent change first). The
preview shows the live pane; Enter jumps there. Columns: time in state, state,
agent, target, project, pane title (Claude Code puts its current task there),
last screen line.

#### Jump key — `prefix A` (`twmux watch next`)

- Jumps to the **newest** `wait`/`done` agent; each further press goes to the
  next older one, wrapping around.
- Starts over at the newest as soon as another agent finishes.
- Skips the pane you are in. Considers only the tmux server you are on (a client
  cannot switch to another server's panes).
- Says "no agent waiting or done" in the status line when there is nothing.
- Reads `agents.json`, so it is as fresh as the last poll (≤2s).

#### Status bar — window icon and summary

| Option | Scope | Value | Visible |
| --- | --- | --- | --- |
| `@twmux_status` (window) | per window | icon of the window's most urgent agent; unset when all idle | In the window list — **only the current session's windows** |
| `@twmux_summary` (global) | per tmux server | count of `wait`/`done` agents in all sessions, e.g. `💬1 ✅2`; unset when none | In `status-right` — from every session |

Both are written only on change and removed when the daemon stops. Icons are
configurable with `status_icons` in `agents.toml` (`""` disables one).
`working` gets a window icon but is not counted in the summary — it does not
need you.

### Outputs reference

| Output | Consumer | Content |
| --- | --- | --- |
| `~/.cache/twmux/agents.tsv` | switcher popup | One row per agent, sorted, no header (columns below) |
| `~/.cache/twmux/agents.json` | `watch next`, scripts | Same rows as JSON objects with `socket`, `pane_id`, `target`, `project`, `agent`, `state`, `state_entered_at` (epoch), `title` |
| `~/.cache/twmux/next.json` | `watch next` | Its cycle position |
| `@twmux_status`, `@twmux_summary` | tmux status bar | See above |
| `~/.cache/twmux/watch.log` | you | Daemon log, rotated (~4 MB max) |

All files are rewritten atomically every `poll_interval`. TSV columns:

| # | Column | Meaning |
| --- | --- | --- |
| 1 | `wait` | Time in the current state (`12s`, `5m04s`, `1h2m`); `-` while `working` |
| 2 | `state` | `wait` \| `done` \| `working` \| `idle` \| `unknown` |
| 3 | `agent` | `agents.toml` section, or the hook's `--agent` name |
| 4 | `target` | `session:window.pane` — the switcher's preview and jump key off this |
| 5 | `project` | Basename of the pane's working directory |
| 6 | `title` | The pane's `pane_title` (set your own with `tmux select-pane -T "label"`) |
| 7 | `last_line` | Last non-blank line of the screen |

Use `agents.json` when you need to address a pane: the TSV target is ambiguous
across tmux sockets, `socket` + `pane_id` is not.

### Troubleshooting

| Symptom | Cause | Check / fix |
| --- | --- | --- |
| No window icons in the status bar | The agents needing you are in **another session**, or all agents are `idle` (no icon) | Look at the `status-right` summary; `tmux list-windows -a -F '#S:#I #{@twmux_status}'` |
| No summary / icons at all | Daemon is old or not running | `twmux watch status` works? `ls -l ~/.cache/twmux/agents.json` recent? Restart the daemon |
| Agent still flaps `working` ↔ `idle` | Its session started before the hooks were installed | `tmux show-options -p -t <pane> \| grep twmux` empty → restart the agent session |
| `prefix A` says "no agent waiting or done" | Nothing in `wait`/`done` on **this** server, or you are already in that pane | `prefix a` shows the full list |
| All rows show the same time in state | The daemon restarted (timers are in memory) | Normal |
| An agent shows `wait` while working | Copilot `notification` matcher not filtering (unverified) | Report it; remove the `notification` hook |
| Pane stays `working` after Ctrl-C | Expected for up to `hook_stale_secs` (10s) | Wait one stale window |
| Hooks blocking prompts / agent never stops | twmux without `mark` installed (exit code 2) | Install the current twmux, or remove the hooks |

### Daemon lifecycle — two ways to start

Both methods rely on `--ensure-running`, which makes startup idempotent via a
PID file at `~/.cache/twmux/watch.pid`. Pick **one**; running both is harmless
(the second one exits silently) but creates noise in logs.

| | tmux `run-shell` | launchd agent |
| --- | --- | --- |
| Lifecycle | Tied to tmux server | Tied to user login |
| Survives `tmux kill-server` | No | Yes |
| Runs without tmux | No | Yes (but has nothing to poll) |
| Auto-restart on crash | Tmux re-runs on next start | KeepAlive throttled at 10s |
| Logs | `~/.cache/twmux/watch.log` (rotated, max ~4 MB) | `~/.cache/twmux/watch.log` (rotated, max ~4 MB) |
| Install effort | One line | Plist + `launchctl load` |

#### Option A — tmux `run-shell` (simpler)

One-time install: add this line near the bottom of `~/.config/tmux/tmux.conf`,
then `tmux source-file ~/.config/tmux/tmux.conf`:

```tmux
run-shell -b 'twmux watch daemon --ensure-running'
```

The `-b` is mandatory — without it tmux blocks on the daemon's never-ending
poll loop and freezes on startup. `--ensure-running` is also mandatory so
that `source-file` reloads don't spawn duplicates.

#### Option B — launchd agent (macOS-native)

One-time install:

```bash
sed "s|USERNAME|$USER|g" examples/dev.sysid.twmux-watch.plist \
  > ~/Library/LaunchAgents/dev.sysid.twmux-watch.plist

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.sysid.twmux-watch.plist
```

> **Why `bootstrap` and not `load`?** Legacy `launchctl load` is brittle on
> modern macOS — if a previous load registered the same label, retrying gives
> the unhelpful `Load failed: 5: Input/output error`. `bootstrap` is the
> documented modern API and gives clear domain errors when something is wrong.

To uninstall (after `launchctl bootout`, see "Operating the daemon" below):

```bash
rm ~/Library/LaunchAgents/dev.sysid.twmux-watch.plist
```

If `twmux` lives somewhere other than `~/.local/bin` (e.g. Homebrew at
`/opt/homebrew/bin`), edit `ProgramArguments[0]` and the `PATH` value in the
plist before loading. Run `which twmux` to find your install path.

### Operating the daemon

`agents.toml` is **read once at startup** — there's no hot reload. After
editing the config (`twmux config`) you have to restart. Here's the full operational matrix:

For brevity below, set `LABEL=dev.sysid.twmux-watch`. The launchd domain for
agents in `~/Library/LaunchAgents/` is **`gui/$(id -u)`** (the user's GUI
session), not `user/$(id -u)` (which is reserved for headless contexts).

| Action | Option A (tmux run-shell) | Option B (launchd) |
| --- | --- | --- |
| **Start** | `twmux watch daemon --ensure-running &` | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$LABEL.plist` |
| **Stop** | `twmux watch stop` | `launchctl bootout gui/$(id -u)/$LABEL` |
| **Restart** | `twmux watch stop && twmux watch daemon --ensure-running &` | `launchctl kickstart -k gui/$(id -u)/$LABEL` |
| **Is it running?** | `twmux watch status` (output ⇒ yes) or `pgrep -f 'twmux watch daemon'` | `launchctl list \| grep $LABEL`<br>(first column is PID; `-` means registered-but-not-running) |
| **Tail logs** | `tail -F ~/.cache/twmux/watch.log` | same |
| **Inspect current TSV** | `cat ~/.cache/twmux/agents.tsv` | same |

Notes:

- `twmux watch stop` is **idempotent** — exits 0 with "no daemon running" if
  the PID file is missing or points at a dead process. Safe to chain in
  scripts.
- `twmux watch stop` also works under Option B, but `launchctl bootout` is
  preferred there: it stops the daemon **and** removes the registration so
  `KeepAlive` cannot relaunch it. Plain `stop` would let launchd respawn it
  within `ThrottleInterval` (10s).
- `kickstart -k` is the only reliable restart for Option B — it kills the
  current invocation and re-runs the program defined in the plist without
  needing to unload/reload.
- The PID file at `~/.cache/twmux/watch.pid` is removed on graceful exit
  (SIGTERM or Ctrl-C). A stale PID file from a crash is auto-detected and
  cleaned on the next `--ensure-running` invocation.
- Logs rotate via Python's `RotatingFileHandler`: 1 MB × 4 files = **~4 MB
  ceiling** regardless of crash-loop noise (`watch.log`, `watch.log.1..3`).

### `agents.toml`

Codex is launched through `node`, so command matching cannot distinguish it
from other Node applications. Add Codex's stable app name to its terminal title
in `~/.codex/config.toml`; newly started Codex sessions then identify themselves
without relying on brittle footer text:

```toml
[tui]
terminal_title = ["spinner", "app-name", "project"]
```

```toml
poll_interval = 2.0     # seconds
hook_stale_secs = 10.0  # see "Combining hook and screen"
status_icons = { wait = "💬", done = "✅", working = "🤖" }  # "" = no icon

[codex_cli]
# Keep this before Claude: both use a braille spinner while working.
title_match = "^(?:[⠀-⣿] )?codex [|] "
re_wait     = "^› [1-9]\\."
re_working  = "^• .+\\(.*esc to interrupt\\)$"
re_idle     = "^› "

[claude_code]
# CC's pane_current_command is its version string (e.g. "2.1.139") — use
# the tmux pane title instead. It starts with "✳ " when idle, and with a
# braille-block spinner glyph (U+2800–U+28FF, e.g. "⠐ ") while processing.
title_match = "^[✳⠀-⣿] "
# Idle: CC's prompt is "❯" followed by NBSP (\xc2\xa0), not ASCII space.
re_idle     = "^❯"
re_wait     = "Do you want to proceed|Do you trust|❯ 1\\."
# Match both the legacy "esc to interrupt" hint and CC's body status
# line: any Dingbats star/sparkle/snowflake glyph (range U+2726–U+274B,
# ✦…❋) at line-start, then "<verb>… (Ns". The glyph anchor avoids false
# positives on plain text containing "… (5s)" (transcripts, doc
# snippets quoted on screen).
re_working  = "esc to interrupt|^[✦-❋] .+… ?\\(\\d+[ms]"

[aider]
cmd_match = "aider"
re_wait   = "^\\? "
re_idle   = "^> "
```

A pane matches an agent if **either** `cmd_match` hits its current command
**or** `title_match` hits its tmux pane title. First-match wins.

Add a new agent by appending a section; no code changes needed.

### Commands

```bash
twmux watch daemon                   # foreground poll loop (Ctrl-C to stop)
twmux watch daemon --ensure-running  # exit silently if a daemon is running
twmux watch stop                     # SIGTERM the running daemon, wait for exit
twmux watch status                   # one-shot poll, print TSV (read-only: no files, no tmux options)
twmux watch next                     # jump to newest wait/done agent; repeat to cycle (bind to prefix A)
twmux --json watch next              # same, reports {"jumped": bool, "pane_id", "state"}
twmux mark STATE [--agent NAME]      # for agent hooks: working | wait | done | clear
twmux config                         # edit agents.toml (poll_interval, hook_stale_secs, status_icons, agents)
```

See **Operating the daemon** above for the full start/stop/restart matrix
covering both `tmux run-shell` and `launchctl`.

## Development

```bash
make install   # Install dependencies
make test      # Run tests
make lint      # Check code style
make format    # Auto-format code
make check     # Run lint + test
```

### Iterating on `twmux watch` (or any installed entry point)

Default `uv tool install` builds and caches a wheel keyed on
`(name, version)` — editing source and reinstalling at the **same version**
won't pick up your changes unless you bump `version` in `pyproject.toml` or
fight the cache with `uv tool uninstall && uv cache clean twmux && uv tool
install`. Plus, a running `launchctl`-managed daemon holds the old binary in
memory until you kickstart it.

The clean workflow is an editable install — `~/.local/bin/twmux` becomes a
stub that imports directly from the source tree, and process restarts pick
up source changes without rebuild:

```bash
uv tool install --editable /Users/$USER/dev/s/private/twmux
```

You only do this once. From then on the cycle is wrapped by Make targets that
auto-detect launchd vs. plain `twmux watch`:

```bash
make watch-restart   # kickstart under launchd, stop+start otherwise
make watch-status    # process + launchd registration + pid file + TSV row count
make watch-logs      # tail -F ~/.cache/twmux/watch.log
make watch-start     # one-shot (no restart logic)
make watch-stop      # bootout under launchd, SIGTERM otherwise
```

Inner loop:

1. Edit source under `src/twmux/`.
2. `make test` (logic tests, no daemon needed).
3. `make watch-restart` to swap the running daemon to the new code.
4. `make watch-logs` in another pane to see the new `daemon started` line and
   any poll exceptions that hit the rotating handler.

To confirm the install is editable (one-time sanity check):

```bash
# Should print a path inside your source tree, NOT inside ~/.local/share/uv/.
/Users/$USER/.local/share/uv/tools/twmux/bin/python -c \
  "import twmux; print(twmux.__file__)"
```

Caveats:

- **Dependency changes** still require a reinstall. If you bump a dep in
  `pyproject.toml`, run `make install` to refresh the tool's isolated venv.
- **`uv tool install` ignores `uv.lock`** and resolves the newest versions
  allowed by `pyproject.toml`. `make install` passes the locked versions as
  constraints (`uv export … | uv tool install -c`), so the tool runs what the
  tests ran against. Upgrade on purpose with `uv lock --upgrade`.
- **Same Python process keeps old code in memory** — Python imports are
  cached per process. Editable means source edits are visible *on next
  process start*, not in an already-running daemon. The kickstart in step 3
  is what makes the new code go live.

## License

MIT


### Prior Art, Inspiration
- [GitHub - pchalasani/claude-code-tools](https://github.com/pchalasani/claude-code-tools)

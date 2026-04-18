---
name: twmux
description: "tmux wrapper for running interactive CLIs, debugging, and agent-to-agent communication. Use when you need interactive tty, shell command execution with output capture, REPL interaction, or orchestrating tasks across multiple terminal sessions. Every command returns structured JSON with {\"ok\": true/false} for reliable programmatic use. Use this skill whenever the task involves terminal sessions, interactive processes, long-running commands, debugging, or coordinating work across shells."
---

# twmux

Race-condition-safe tmux wrapper for coding agents. All commands return a consistent JSON envelope: `{"ok": true, ...}` on success, `{"ok": false, "error": "..."}` on failure. Exit code 0 = success, 1 = error. All JSON goes to stdout.

## When to Use

- Interactive REPLs (Python, Node, psql, mysql)
- Debugging sessions (gdb, lldb, pdb, ipdb)
- Long-running commands with output capture
- Agent-to-agent communication
- Orchestrating tasks across multiple terminal sessions
- Test automation requiring shell interaction

## Quick Reference

| Command | Purpose | Key Options |
|---------|---------|-------------|
| `new` | Create session | `-c command` |
| `send` | Send text safely | `--no-enter`, `--delay` |
| `exec` | Run shell command | `--timeout` |
| `capture` | Get pane content | `-n lines` |
| `wait-idle` | Wait for output stability | `--timeout`, `--interval` |
| `launch` | Split pane | `-v` vertical, `-c command`, `--exec` (command IS pane PID 1), `--focus` (move cursor to new pane) |
| `wait-pane` | Block until pane is gone | `--timeout`, `--interval` |
| `interrupt` | Send Ctrl+C | |
| `escape` | Send Escape key | |
| `kill` | Kill pane | |
| `move-pane` | Move pane between sessions | `-b`, `-h`, `-f`, `-l` |
| `move-window` | Move window between sessions | |
| `kill-session` | Kill session | |
| `kill-server` | Kill server | |
| `status` | List sessions/panes | `--all` |

All commands support `--json` for programmatic output and `-t target` for pane targeting.

## Instructions
- You MUST NOT use the "--socket" option.
- You MUST always use `--json` when consuming output programmatically.
- Check the `ok` field first in every JSON response before accessing other fields.

## JSON Envelope

Every command follows this contract:

```
Success: {"ok": true,  ...command-specific-fields}   exit code 0
Error:   {"ok": false, "error": "message"}            exit code 1
```

No Rich markup or ANSI escapes in JSON values. One JSON object per invocation.

## Target Syntax

```
%5           Pane ID (recommended - get via 'twmux --json status')
main:0.1     Session "main", window 0, pane 1
main:0       Session "main", window 0, active pane
:0.1         First session, window 0, pane 1
(empty)      First session, active window/pane
```

## Bootstrap: Discover and Connect

An agent encountering twmux for the first time can self-configure:

```bash
# 1. Discover available commands
twmux --json
# {"ok": true, "commands": [{"name": "send", "description": "..."}, ...]}

# 2. Discover existing sessions and pane IDs
twmux --json status
# {"ok": true, "sockets": [{"socket": "claude", "sessions": [...]}]}

# 3. Create a session if none exist
twmux --json new myapp
# {"ok": true, "session": "myapp", "socket": "claude", "pane_id": "%0", "monitor_cmd": "..."}
```

## Core Workflows

### 1. Shell Commands (exec)

For commands that terminate and return exit codes:

```bash
# Run command and get structured output
twmux --json exec -t %5 "make test"
# {"ok": true, "output": "...", "exit_code": 0, "timed_out": false}

# Long-running command with timeout
twmux --json exec -t %5 "cargo build --release" --timeout 120

# Check for failure
twmux --json exec -t %5 "npm test"
# exit_code in the JSON tells you if the *command* failed (ok is still true — the exec itself succeeded)
```

**When to use `exec`**: Build/test commands, scripts, any command that exits.

### 2. Interactive REPLs (send + wait-idle + capture)

For Python, Node, databases, and other interactive shells:

```bash
# Create session with Python REPL (ALWAYS use PYTHON_BASIC_REPL!)
twmux new python-session -c "PYTHON_BASIC_REPL=1 python3 -q"

# Send code to REPL
twmux send -t %5 "x = 42"

# Wait for output to stabilize
twmux wait-idle -t %5

# Capture result
twmux --json capture -t %5 -n 20
# {"ok": true, "content": [">>> x = 42", ">>> "]}

# Multi-line code (use --no-enter for intermediate lines)
twmux send -t %5 "def greet(name):" --no-enter
twmux send -t %5 "    return f'Hello, {name}!'"  # Enter sent here
```

**CRITICAL**: Always set `PYTHON_BASIC_REPL=1` for Python. The fancy REPL interferes with send-keys.

### 3. Debugging (gdb/lldb/pdb)

```bash
# Start debugger
twmux new debug-session -c "lldb ./myprogram"

# Disable pagination (gdb)
twmux send -t %5 "set pagination off"
twmux wait-idle -t %5

# Set breakpoint and run
twmux send -t %5 "b main"
twmux wait-idle -t %5
twmux send -t %5 "run"
twmux wait-idle -t %5 --timeout 60
twmux capture -t %5

# Interrupt running program
twmux interrupt -t %5
```

### 4. Agent-to-Agent Communication

To communicate with another Claude Code instance:

```bash
# Create session for the other agent
twmux new agent-helper

# Send instructions (NOT exec - use send for interactive agents)
twmux send -t %5 "Review the authentication module in src/auth/"

# Wait for agent to finish processing
twmux wait-idle -t %5 --timeout 300 --interval 2

# Get the response
twmux --json capture -t %5 -n 100
# {"ok": true, "content": ["...", "..."]}
```

### 5. Multiple Panes and Cross-Session Moves

```bash
# Create base session
twmux new multi-pane

# Split horizontally (new pane below)
twmux --json launch -t %5
# {"ok": true, "pane_id": "%6"}

# Split vertically (new pane to the right)
twmux --json launch -t %5 -v

# Split and move cursor into the new pane (default leaves focus on original)
twmux --json launch -t %5 --focus
# {"ok": true, "pane_id": "%7", "focused": true}

# Move a pane to another session
twmux --json move-pane -t %6 other-session
# {"ok": true, "pane_id": "%6", "destination_session": "other-session", "new_window": true}

# Move entire window to another session
twmux --json move-window -t %5 other-session
```

### 6. Interactive TUIs and Editors (launch --exec + wait-pane)

For launching an editor or TUI that the user interacts with, then blocking
until they close it:

```bash
# Split with the editor as pane's PID 1 (not wrapped in a shell).
# The pane dies automatically when the editor exits.
EDITOR_PANE=$(twmux --json launch -t %5 --exec -c "nvim /tmp/draft.md" \
  | jq -r '.pane_id')

# Block until the user closes the editor.
twmux wait-pane -t "$EDITOR_PANE"
# {"ok": true, "gone": true, "elapsed": 42.13}

# Now the saved file is ready to read.
cat /tmp/draft.md
```

**When to use `--exec` vs plain `-c`:**
- Plain `-c "<cmd>"` types the command into a shell — the pane survives the
  command. Use this for interactive REPLs (Python, psql) where you want to
  keep sending input.
- `--exec -c "<cmd>"` makes the command the pane's PID 1 — pane dies on exit.
  Use this for one-shot TUIs (editors, `less`, `fzf`) where "pane gone" is the
  signal you need.

`wait-pane` is idempotent: if the pane is already gone when you call it,
it returns immediately with `elapsed: 0`. Use `--timeout N` to bound the
wait; default `0` means wait forever.

## User Communication

**ALWAYS** tell the user how to monitor the session immediately after creation:

```
To monitor this session:
  tmux -L claude attach -t <session-name>
```

Print this at session start AND at the end of your tool loop.

## Socket Isolation

twmux uses the `claude` socket by default, isolating agent sessions from user's personal tmux.

- Default socket: `claude` (no flag needed)
- Custom agent socket: `twmux -L claude-isolated new test`
- Non-agent socket (requires `--force`): `twmux --force -L my-socket status`

## Common Patterns

### Reliable error handling

```bash
result=$(twmux --json exec -t %5 "npm install")
ok=$(echo "$result" | jq -r '.ok')
if [ "$ok" != "true" ]; then
  error=$(echo "$result" | jq -r '.error')
  echo "twmux error: $error"
  exit 1
fi
exit_code=$(echo "$result" | jq -r '.exit_code')
if [ "$exit_code" -ne 0 ]; then
  echo "Command failed with exit code $exit_code"
fi
```

### Wait for specific prompt

```bash
twmux send -t %5 "python3 -q"

# Poll until prompt appears
while true; do
  result=$(twmux --json capture -t %5)
  if echo "$result" | jq -r '.content[]' | grep -q '>>>'; then
    break
  fi
  sleep 0.5
done
```

### Graceful shutdown

```bash
# Try clean exit first
twmux send -t %5 "exit"
twmux wait-idle -t %5 --timeout 5

# Force kill if needed
twmux kill -t %5
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Command not found | `uv tool install twmux` |
| `{"ok": false, "error": "...socket..."}` | Add `--force` for non-claude sockets |
| Python REPL garbled | Use `PYTHON_BASIC_REPL=1` |
| Enter not registered | Increase `--delay` (e.g., `--delay 0.1`) |
| Command timeout | Increase `--timeout` |
| No sessions found | Check socket with `twmux --json status --all` |

## Cleanup

```bash
# Kill specific pane
twmux kill -t %5

# Kill session
twmux kill-session mysession

# Kill entire server (all sessions on socket)
twmux kill-server
```

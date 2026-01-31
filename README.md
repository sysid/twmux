<p align="center">
  <img src="docs/logo_264x264.png" alt="twmux logo" width="200">
</p>

Race-condition-safe tmux wrapper for coding agents.

## Features

- **Race-condition-safe send** - Verifies commands are received before sending Enter
- **Execute and capture** - Run commands and get output with exit codes
- **Marker-based execution** - Reliable output capture using unique markers
- **Wait-idle detection** - Wait until pane output stabilizes
- **JSON output** - Programmatic interface for all commands
- **Flexible targeting** - Pane IDs or session:window.pane syntax
- **Pane management** - Launch, kill, interrupt, and escape

Nothing you couldn't do with bare "tmux" skill, but much more reliable with agent use.

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
| `-L, --socket NAME` | Connect to specific tmux socket |
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
twmux launch -t %5                      # Split below
twmux launch -t %5 -v                   # Split right (vertical)
twmux launch -t %5 -c "python3"         # Split and run command
```

### kill - Kill pane

```bash
twmux kill -t %5
```

### status - Show tmux state

```bash
twmux status
twmux --json status
```

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
# Returns: {"pane_id": "%12"}

# Send commands to the new pane
twmux send -t %12 "print('hello')"
twmux wait-idle -t %12

# Capture output
twmux capture -t %12 -n 10

# Execute and get result
twmux --json exec -t %12 "print(1+1)"
# Returns: {"output": "2", "exit_code": 0, "timed_out": false}

# Clean up
twmux kill -t %12
```

## JSON Output

All commands support `--json` for programmatic use:

```bash
$ twmux --json exec -t %5 "echo hello"
{"output": "hello", "exit_code": 0, "timed_out": false}

$ twmux --json send -t %5 "test"
{"success": true, "attempts": 1}

$ twmux --json status
{
  "sessions": [
    {
      "session_id": "$0",
      "session_name": "main",
      "windows": [...]
    }
  ]
}
```

## How It Works

### Race-Condition-Safe Send

The `send` command:
1. Sends text without Enter
2. Waits (configurable delay)
3. Captures pane content
4. Sends Enter
5. Verifies content changed
6. Retries if needed

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

## Development

```bash
make install   # Install dependencies
make test      # Run tests
make lint      # Check code style
make format    # Auto-format code
make check     # Run lint + test
```

## License

MIT


### Prior Art, Inspiration
- [GitHub - pchalasani/claude-code-tools](https://github.com/pchalasani/claude-code-tools)

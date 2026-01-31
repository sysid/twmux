"""twmux CLI entry point."""

import json as json_lib
from typing import Annotated

import typer
from rich import print as rprint

from twmux import __version__

app = typer.Typer(
    help="Race-condition-safe tmux wrapper for coding agents.",
    no_args_is_help=True,
    epilog="""
[bold]Target Syntax (-t)[/bold]
  %5           Pane ID (recommended, get via 'twmux status')
  main:0.1     Session "main", window 0, pane 1
  main:0       Session "main", window 0, active pane
  :0.1         First session, window 0, pane 1
  (empty)      First session, active window/pane

[bold]JSON Output[/bold]
  Use --json for programmatic output (all commands).
""",
)

# Global options
json_output: bool = False
socket_name: str | None = None


@app.callback()
def main(
    json: Annotated[bool, typer.Option("--json", help="Output as JSON for programmatic use")] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose output")] = False,
    socket: Annotated[str | None, typer.Option("-L", "--socket", help="tmux socket name")] = None,
) -> None:
    """Global options applied to all commands."""
    global json_output, socket_name
    json_output = json
    socket_name = socket


def get_pane(target: str):
    """Resolve target to libtmux Pane object."""
    from libtmux import Server

    server = Server(socket_name=socket_name)

    # Handle pane ID directly (e.g., %5)
    if target.startswith("%"):
        for session in server.sessions:
            for window in session.windows:
                for pane in window.panes:
                    if pane.pane_id == target:
                        return pane
        raise typer.BadParameter(f"Pane not found: {target}")

    # Handle session:window.pane format
    parts = target.split(":")
    session_name = parts[0] if parts[0] else None

    # Find session
    if session_name:
        sessions = [s for s in server.sessions if s.session_name == session_name]
        if not sessions:
            raise typer.BadParameter(f"Session not found: {session_name}")
        session = sessions[0]
    else:
        session = server.sessions[0] if server.sessions else None
        if not session:
            raise typer.BadParameter("No tmux sessions found")

    # Parse window.pane
    if len(parts) > 1 and parts[1]:
        window_pane = parts[1].split(".")
        window_idx = int(window_pane[0]) if window_pane[0] else 0
        pane_idx = int(window_pane[1]) if len(window_pane) > 1 else 0

        window = session.windows[window_idx]
        return window.panes[pane_idx]

    return session.active_window.active_pane


def output_result(data: dict) -> None:
    """Output result in appropriate format."""
    if json_output:
        print(json_lib.dumps(data))
    else:
        for key, value in data.items():
            rprint(f"{key}: {value}")


@app.command(rich_help_panel="Info")
def version() -> None:
    """Show twmux version.

    JSON: {"version": str}
    """
    if json_output:
        print(json_lib.dumps({"version": __version__}))
    else:
        print(f"twmux {__version__}")


@app.command(
    rich_help_panel="Core Operations",
    epilog="""
[bold]Examples[/bold]
  twmux send -t %5 "echo hello"
  twmux send -t main:0.1 "make test" --delay 0.1
  twmux send -t %5 "partial" --no-enter
""",
)
def send(
    text: Annotated[str, typer.Argument(help="Text to send to the pane")],
    target: Annotated[str, typer.Option("-t", "--target", help="Target pane", show_default=True)] = "",
    no_enter: Annotated[bool, typer.Option("--no-enter", help="Send text without pressing Enter")] = False,
    delay: Annotated[float, typer.Option("--delay", help="Delay before Enter (seconds)", show_default=True)] = 0.05,
) -> None:
    """Send text to pane with race-condition-safe Enter handling.

    Sends text, waits, then sends Enter and verifies the pane content changed.
    Retries Enter if verification fails (solves the "lost Enter" problem).

    JSON: {"success": bool, "attempts": int}
    Exit: 0 if sent, 1 if Enter verification failed after retries.
    """
    from twmux.lib.safe_input import send_safe

    pane = get_pane(target)
    result = send_safe(pane, text, enter=not no_enter, enter_delay=delay)

    output_result({"success": result.success, "attempts": result.attempts})

    if not result.success:
        raise typer.Exit(1)


@app.command(
    name="exec",
    rich_help_panel="Core Operations",
    epilog="""
[bold]Examples[/bold]
  twmux exec -t %5 "ls -la"
  twmux --json exec -t main:0 "make test" --timeout 60
""",
)
def exec_cmd(
    command: Annotated[str, typer.Argument(help="Shell command to execute")],
    target: Annotated[str, typer.Option("-t", "--target", help="Target pane", show_default=True)] = "",
    timeout: Annotated[float, typer.Option("--timeout", help="Max wait time (seconds)", show_default=True)] = 30.0,
) -> None:
    """Execute command and capture output with exit code.

    Wraps command in unique markers, runs it, then extracts output between
    markers. Use for commands that produce output and terminate. Check
    exit_code and timed_out in output to determine success.

    JSON: {"output": str, "exit_code": int, "timed_out": bool}
    Exit: Always 0 (check exit_code/timed_out in JSON for command result).
    """
    from twmux.lib.execution import execute

    pane = get_pane(target)
    result = execute(pane, command, timeout=timeout)

    output_result(
        {
            "output": result.output,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        }
    )


@app.command(
    rich_help_panel="Core Operations",
    epilog="""
[bold]Examples[/bold]
  twmux capture -t %5
  twmux capture -t %5 -n 50
  twmux --json capture -t main:0
""",
)
def capture(
    target: Annotated[str, typer.Option("-t", "--target", help="Target pane", show_default=True)] = "",
    lines: Annotated[int | None, typer.Option("-n", "--lines", help="Limit to last N lines")] = None,
) -> None:
    """Capture current visible pane content.

    Returns text currently displayed in the pane's visible area.
    Use -n to limit to last N lines (useful for long outputs).

    JSON: {"content": [str, ...]}
    Exit: Always 0.
    """
    pane = get_pane(target)

    if lines:
        content = pane.capture_pane(start=-lines)
    else:
        content = pane.capture_pane()

    if json_output:
        print(json_lib.dumps({"content": content}))
    else:
        print("\n".join(content))


@app.command(
    name="wait-idle",
    rich_help_panel="Core Operations",
    epilog="""
[bold]Examples[/bold]
  twmux wait-idle -t %5
  twmux wait-idle -t %5 --timeout 10 --interval 0.1
""",
)
def wait_idle(
    target: Annotated[str, typer.Option("-t", "--target", help="Target pane", show_default=True)] = "",
    timeout: Annotated[float, typer.Option("--timeout", help="Max wait time (seconds)", show_default=True)] = 30.0,
    interval: Annotated[float, typer.Option("--interval", help="Poll interval (seconds)", show_default=True)] = 0.2,
) -> None:
    """Wait until pane output stops changing.

    Polls pane content at interval, succeeds when consecutive polls return
    identical content. Use after send to wait for command completion.

    JSON: {"idle": bool, "elapsed": float}
    Exit: 0 if stabilized, 1 if timeout reached.
    """
    from twmux.lib.safe_input import wait_for_idle

    pane = get_pane(target)
    result = wait_for_idle(pane, poll_interval=interval, timeout=timeout)

    output_result({"idle": result.idle, "elapsed": round(result.elapsed, 3)})

    if not result.idle:
        raise typer.Exit(1)


@app.command(
    rich_help_panel="Pane Control",
    epilog="""
[bold]Example[/bold]
  twmux interrupt -t %5
""",
)
def interrupt(
    target: Annotated[str, typer.Option("-t", "--target", help="Target pane", show_default=True)] = "",
) -> None:
    """Send Ctrl+C to interrupt running process.

    Equivalent to pressing Ctrl+C. Use to cancel long-running commands
    or exit interactive prompts expecting SIGINT.

    JSON: {"interrupted": true}
    Exit: Always 0.
    """
    pane = get_pane(target)
    pane.send_keys("C-c", enter=False)

    output_result({"interrupted": True})


@app.command(
    rich_help_panel="Pane Management",
    epilog="""
[bold]Examples[/bold]
  twmux launch -t %5                    # Split below (horizontal)
  twmux launch -t %5 -v                 # Split right (vertical)
  twmux launch -t %5 -c "python3"       # Split and run command
""",
)
def launch(
    target: Annotated[str, typer.Option("-t", "--target", help="Pane to split", show_default=True)] = "",
    command: Annotated[str | None, typer.Option("-c", "--command", help="Command to run in new pane")] = None,
    vertical: Annotated[bool, typer.Option("-v", "--vertical", help="Split right instead of below")] = False,
) -> None:
    """Create new pane by splitting target pane.

    Returns new pane ID for use in subsequent commands. Default split is
    horizontal (below); use -v for vertical (right).

    JSON: {"pane_id": str}
    Exit: Always 0.
    """
    from libtmux.constants import PaneDirection

    pane = get_pane(target)

    direction = PaneDirection.Right if vertical else PaneDirection.Below
    new_pane = pane.split(direction=direction)

    if command:
        new_pane.send_keys(command, enter=True)

    output_result({"pane_id": new_pane.pane_id})


@app.command(
    rich_help_panel="Pane Management",
    epilog="""
[bold]Example[/bold]
  twmux kill -t %5
""",
)
def kill(
    target: Annotated[str, typer.Option("-t", "--target", help="Pane to kill", show_default=True)] = "",
) -> None:
    """Terminate pane and its processes.

    Immediately kills the pane. Irreversible.

    JSON: {"killed": true}
    Exit: Always 0.
    """
    pane = get_pane(target)
    pane.kill()

    output_result({"killed": True})


@app.command(
    rich_help_panel="Pane Control",
    epilog="""
[bold]Example[/bold]
  twmux escape -t %5
""",
)
def escape(
    target: Annotated[str, typer.Option("-t", "--target", help="Target pane", show_default=True)] = "",
) -> None:
    """Send Escape key to pane.

    Useful for exiting vim insert mode, canceling prompts,
    or clearing partial input.

    JSON: {"escaped": true}
    Exit: Always 0.
    """
    pane = get_pane(target)
    pane.send_keys("Escape", enter=False)

    output_result({"escaped": True})


@app.command(
    rich_help_panel="Info",
    epilog="""
[bold]Examples[/bold]
  twmux status
  twmux --json status
""",
)
def status() -> None:
    """Show all tmux sessions, windows, and panes.

    Lists hierarchy with pane IDs (%N format) for use with -t flag.
    Run this first to discover pane IDs for other commands.

    JSON: {"sessions": [{session_id, session_name, windows: [...]}]}
    Exit: Always 0.
    """
    from libtmux import Server

    server = Server(socket_name=socket_name)

    sessions_data = []
    for session in server.sessions:
        windows_data = []
        for window in session.windows:
            panes_data = [{"pane_id": p.pane_id, "pane_index": p.pane_index} for p in window.panes]
            windows_data.append(
                {
                    "window_id": window.window_id,
                    "window_index": window.window_index,
                    "window_name": window.window_name,
                    "panes": panes_data,
                }
            )
        sessions_data.append(
            {
                "session_id": session.session_id,
                "session_name": session.session_name,
                "windows": windows_data,
            }
        )

    if json_output:
        print(json_lib.dumps({"sessions": sessions_data}, indent=2))
    else:
        for s in sessions_data:
            rprint(f"[bold]{s['session_name']}[/bold] ({s['session_id']})")
            for w in s["windows"]:
                rprint(f"  {w['window_index']}: {w['window_name']} ({w['window_id']})")
                for p in w["panes"]:
                    rprint(f"    .{p['pane_index']}: {p['pane_id']}")


if __name__ == "__main__":
    app()

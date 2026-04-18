"""twmux CLI entry point."""

import json as json_lib
from typing import Annotated

import typer
from rich import print as rprint

from twmux.lib.safety import DEFAULT_SOCKET, SocketValidationError, validate_socket

__version__ = "0.6.0"

app = typer.Typer(
    help="Race-condition-safe tmux wrapper for coding agents.",
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
socket_name: str = DEFAULT_SOCKET
force_socket: bool = False


def print_version() -> None:
    """Print version information."""
    if json_output:
        output_result({"version": __version__})
    else:
        print(f"twmux {__version__}")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    json: Annotated[
        bool, typer.Option("--json", help="Output as JSON for programmatic use")
    ] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose output")] = False,
    version: Annotated[bool, typer.Option("-V", "--version", help="Show version")] = False,
    socket: Annotated[
        str, typer.Option("-L", "--socket", help="tmux socket name")
    ] = DEFAULT_SOCKET,
    force: Annotated[bool, typer.Option("--force", help="Allow non-agent sockets")] = False,
) -> None:
    """Global options applied to all commands."""
    global json_output, socket_name, force_socket
    json_output = json
    socket_name = socket
    force_socket = force

    if ctx.invoked_subcommand is None:
        if version:
            print_version()
            raise typer.Exit(0)
        if json_output:
            # Machine-discoverable command listing for agents
            commands = []
            for name in sorted(ctx.command.list_commands(ctx)):
                cmd = ctx.command.get_command(ctx, name)
                commands.append({"name": name, "description": (cmd.help or "").split("\n")[0]})
            output_result({"commands": commands})
            raise typer.Exit(0)
        typer.echo(ctx.get_help())
        raise typer.Exit(0)

    # Validate socket early
    try:
        validate_socket(socket_name, force_socket)
    except SocketValidationError as e:
        error_result(str(e))
        raise typer.Exit(1)


def get_pane(target: str):
    """Resolve target to libtmux Pane object.

    Uses error_result() for all error paths, ensuring JSON-compatible errors
    when --json is active. Raises typer.Exit(1) on any resolution failure.
    """
    from libtmux import Server

    try:
        server = Server(socket_name=socket_name)
    except Exception as e:
        error_result(f"Cannot connect to tmux server on socket '{socket_name}': {e}")
        raise typer.Exit(1)

    # Handle pane ID directly (e.g., %5)
    if target.startswith("%"):
        for session in server.sessions:
            for window in session.windows:
                for pane in window.panes:
                    if pane.pane_id == target:
                        return pane
        error_result(f"Pane not found: {target}")
        raise typer.Exit(1)

    # Handle session:window.pane format
    parts = target.split(":")
    session_name = parts[0] if parts[0] else None

    # Find session
    if session_name:
        sessions = [s for s in server.sessions if s.session_name == session_name]
        if not sessions:
            error_result(f"Session not found: {session_name}")
            raise typer.Exit(1)
        session = sessions[0]
    else:
        session = server.sessions[0] if server.sessions else None
        if not session:
            error_result("No tmux sessions found")
            raise typer.Exit(1)

    # Parse window.pane
    if len(parts) > 1 and parts[1]:
        try:
            window_pane = parts[1].split(".")
            window_idx = int(window_pane[0]) if window_pane[0] else 0
            pane_idx = int(window_pane[1]) if len(window_pane) > 1 else 0
            window = session.windows[window_idx]
            return window.panes[pane_idx]
        except (ValueError, IndexError) as e:
            error_result(f"Invalid target '{target}': {e}")
            raise typer.Exit(1)

    return session.active_window.active_pane


def output_result(data: dict) -> None:
    """Output result in appropriate format.

    JSON mode: injects ok=True into the response envelope.
    Human mode: prints key: value pairs with Rich formatting.
    """
    if json_output:
        print(json_lib.dumps({"ok": True, **data}))
    else:
        for key, value in data.items():
            rprint(f"{key}: {value}")


def error_result(msg: str) -> None:
    """Output error in appropriate format.

    JSON mode: emits {"ok": false, "error": msg} to stdout, stripping Rich markup.
    Human mode: prints Rich-formatted error to stdout.
    """
    if json_output:
        # Strip Rich markup from message for clean JSON
        from rich.text import Text

        clean_msg = Text.from_markup(msg).plain if "[" in msg else msg
        print(json_lib.dumps({"ok": False, "error": clean_msg}))
    else:
        rprint(f"[red]Error:[/red] {msg}")


def resolve_destination(server, destination: str):
    """Parse destination and find target session.

    Returns (session, window_spec) or emits error and raises typer.Exit(1).
    """
    parts = destination.split(":", 1)
    session_name = parts[0]
    window_spec = parts[1] if len(parts) > 1 and parts[1] else None

    sessions = [s for s in server.sessions if s.session_name == session_name]
    if not sessions:
        error_result(f"Session not found: {session_name}")
        raise typer.Exit(1)

    dest_session = sessions[0]

    return dest_session, window_spec


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
    target: Annotated[
        str, typer.Option("-t", "--target", help="Target pane", show_default=True)
    ] = "",
    no_enter: Annotated[
        bool, typer.Option("--no-enter", help="Send text without pressing Enter")
    ] = False,
    delay: Annotated[
        float,
        typer.Option("--delay", help="Delay before Enter (seconds)", show_default=True),
    ] = 0.05,
) -> None:
    """Send text to pane with race-condition-safe Enter handling.

    Sends text, waits, then sends Enter and verifies the pane content changed.
    Retries Enter if verification fails (solves the "lost Enter" problem).

    JSON: {"ok": true, "success": bool, "attempts": int}
    Error: {"ok": false, "error": str}
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
    target: Annotated[
        str, typer.Option("-t", "--target", help="Target pane", show_default=True)
    ] = "",
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Max wait time (seconds)", show_default=True),
    ] = 30.0,
) -> None:
    """Execute command and capture output with exit code.

    Wraps command in unique markers, runs it, then extracts output between
    markers. Use for commands that produce output and terminate. Check
    exit_code and timed_out in output to determine success.

    JSON: {"ok": true, "output": str, "exit_code": int, "timed_out": bool}
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
    target: Annotated[
        str, typer.Option("-t", "--target", help="Target pane", show_default=True)
    ] = "",
    lines: Annotated[
        int | None, typer.Option("-n", "--lines", help="Limit to last N lines")
    ] = None,
) -> None:
    """Capture current visible pane content.

    Returns text currently displayed in the pane's visible area.
    Use -n to limit to last N lines (useful for long outputs).

    JSON: {"ok": true, "content": [str, ...]}
    Exit: Always 0.
    """
    pane = get_pane(target)

    if lines:
        content = pane.capture_pane(start=-lines)
    else:
        content = pane.capture_pane()

    if json_output:
        output_result({"content": content})
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
    target: Annotated[
        str, typer.Option("-t", "--target", help="Target pane", show_default=True)
    ] = "",
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Max wait time (seconds)", show_default=True),
    ] = 30.0,
    interval: Annotated[
        float,
        typer.Option("--interval", help="Poll interval (seconds)", show_default=True),
    ] = 0.2,
) -> None:
    """Wait until pane output stops changing.

    Polls pane content at interval, succeeds when consecutive polls return
    identical content. Use after send to wait for command completion.

    JSON: {"ok": true, "idle": bool, "elapsed": float}
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
    target: Annotated[
        str, typer.Option("-t", "--target", help="Target pane", show_default=True)
    ] = "",
) -> None:
    """Send Ctrl+C to interrupt running process.

    Equivalent to pressing Ctrl+C. Use to cancel long-running commands
    or exit interactive prompts expecting SIGINT.

    JSON: {"ok": true, "interrupted": true}
    Exit: Always 0.
    """
    pane = get_pane(target)
    pane.send_keys("C-c", enter=False)

    output_result({"interrupted": True})


@app.command(
    rich_help_panel="Pane Management",
    epilog="""
[bold]Examples[/bold]

twmux launch -t %5                          # Split below (horizontal)

twmux launch -t %5 -v                       # Split right (vertical)

twmux launch -t %5 -c "python3"             # Split, run command in shell

twmux launch -t %5 --exec -c "nvim /tmp/x"  # Command IS pane process; pane dies on exit
""",
)
def launch(
    target: Annotated[
        str, typer.Option("-t", "--target", help="Pane to split", show_default=True)
    ] = "",
    command: Annotated[
        str | None, typer.Option("-c", "--command", help="Command to run in new pane")
    ] = None,
    vertical: Annotated[
        bool, typer.Option("-v", "--vertical", help="Split right instead of below")
    ] = False,
    exec_mode: Annotated[
        bool,
        typer.Option(
            "--exec",
            help="Run command as pane's PID 1 (no shell wrap); pane dies when command exits",
        ),
    ] = False,
) -> None:
    """Create new pane by splitting target pane.

    Returns new pane ID for use in subsequent commands. Default split is
    horizontal (below); use -v for vertical (right).

    With --exec, the command replaces the shell as the pane's root process.
    The pane terminates automatically when the command exits — useful for
    editors or TUIs combined with `twmux wait-pane`. Requires -c.

    JSON: {"ok": true, "pane_id": str}
    Exit: Always 0 on success; 1 if --exec is used without -c.
    """
    from libtmux.constants import PaneDirection
    from libtmux.exc import TmuxObjectDoesNotExist

    if exec_mode and not command:
        error_result("--exec requires -c/--command")
        raise typer.Exit(1)

    pane = get_pane(target)

    direction = PaneDirection.Right if vertical else PaneDirection.Below
    if exec_mode:
        try:
            new_pane = pane.split(direction=direction, shell=command)
        except TmuxObjectDoesNotExist:
            # libtmux splits with -dP and parses the reply to learn the new pane id;
            # if the command exits before that lookup resolves, the id is gone.
            error_result(
                "command exited before pane could be registered; "
                "use a longer-running command or omit --exec"
            )
            raise typer.Exit(1)
    else:
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
    target: Annotated[
        str, typer.Option("-t", "--target", help="Pane to kill", show_default=True)
    ] = "",
) -> None:
    """Terminate pane and its processes.

    Immediately kills the pane. Irreversible.

    JSON: {"ok": true, "killed": true}
    Exit: Always 0.
    """
    pane = get_pane(target)
    pane.kill()

    output_result({"killed": True})


@app.command(
    name="wait-pane",
    rich_help_panel="Pane Management",
    epilog="""
[bold]Examples[/bold]

twmux wait-pane -t %5                      # Block forever until pane dies

twmux wait-pane -t %5 --timeout 60         # Error after 60s if still alive

twmux wait-pane -t %99999                  # Non-existent pane => gone immediately
""",
)
def wait_pane(
    target: Annotated[
        str, typer.Option("-t", "--target", help="Pane to wait on", show_default=True)
    ] = "",
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            help="Max wait time in seconds (0 = forever)",
            show_default=True,
        ),
    ] = 0.0,
    interval: Annotated[
        float,
        typer.Option("--interval", help="Poll interval (seconds)", show_default=True),
    ] = 0.2,
) -> None:
    """Block until pane no longer exists.

    Polls the tmux server until the target pane is gone (e.g. its process
    exited, or it was killed). Pairs with `launch --exec` to wait for an
    editor or TUI launched in a split to close.

    Idempotent: if the pane doesn't exist at call time, returns immediately.

    JSON: {"ok": true, "gone": true, "elapsed": float}
    Exit: 0 when pane is gone; 1 on timeout.
    """
    import time

    from libtmux import Server
    from libtmux.exc import LibTmuxException

    if not target:
        error_result("--target is required")
        raise typer.Exit(1)

    try:
        server = Server(socket_name=socket_name)
    except Exception as e:
        error_result(f"Cannot connect to tmux server on socket '{socket_name}': {e}")
        raise typer.Exit(1)

    def pane_exists() -> bool:
        try:
            return any(p.pane_id == target for p in server.panes)
        except LibTmuxException:
            return False

    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if not pane_exists():
            output_result({"gone": True, "elapsed": round(elapsed, 3)})
            return
        if timeout > 0 and elapsed >= timeout:
            error_result(f"timeout after {round(elapsed, 3)}s (limit {timeout}s)")
            raise typer.Exit(1)
        time.sleep(interval)


@app.command(
    name="move-pane",
    rich_help_panel="Pane Management",
    context_settings={"help_option_names": ["--help"]},
    epilog="""
[bold]Examples[/bold]

twmux move-pane -t %5 debug              # New window in "debug"

twmux move-pane -t %5 debug:0            # Join window 0 in "debug"

twmux move-pane -t %5 debug:0 -b -h      # Join left of window 0, horizontal

twmux move-pane -t %5 debug:0 -h -l 30%  # Horizontal, 30% width

twmux move-pane -t %5 debug:0 -f -b      # Full-width, above target
""",
)
def move_pane(
    destination: Annotated[str, typer.Argument(help="Destination: session or session:window")],
    target: Annotated[
        str, typer.Option("-t", "--target", help="Source pane", show_default=True)
    ] = "",
    before: Annotated[
        bool, typer.Option("-b", "--before", help="Place pane before (left/above) target")
    ] = False,
    horizontal: Annotated[
        bool, typer.Option("-h", "--horizontal", help="Horizontal (side-by-side) split")
    ] = False,
    full: Annotated[
        bool, typer.Option("-f", "--full", help="Use full window width/height")
    ] = False,
    size: Annotated[
        str | None,
        typer.Option("-l", "--size", help="Pane size: lines/columns or %% (e.g. '30%%')"),
    ] = None,
) -> None:
    """Move pane to another session.

    Session-only destination creates a new window. Session:window joins
    existing window. Positioning flags (-b, -h, -f, -l) apply only when
    joining an existing window.

    JSON: {"ok": true, "pane_id": str, "destination_session": str, "new_window": bool}
    Exit: 0 success, 1 if same-session or destination not found.
    """
    pane = get_pane(target)
    dest_session, window_spec = resolve_destination(pane.server, destination)

    if window_spec is not None:
        # join-pane: move pane into existing window
        args = [
            "join-pane",
            "-d",
            "-s",
            pane.pane_id,
            "-t",
            f"{dest_session.session_id}:{window_spec}",
        ]
        if before:
            args.append("-b")
        if full:
            args.append("-f")
        if horizontal:
            args.append("-h")
        if size is not None:
            args.extend(["-l", size])
        pane.server.cmd(*args)
        new_window = False
    else:
        # break-pane: move pane to new window in destination session
        pane.server.cmd("break-pane", "-d", "-s", pane.pane_id, "-t", f"{dest_session.session_id}:")
        new_window = True

    output_result(
        {
            "pane_id": pane.pane_id,
            "destination_session": dest_session.session_name,
            "new_window": new_window,
        }
    )


@app.command(
    rich_help_panel="Pane Control",
    epilog="""
[bold]Example[/bold]
  twmux escape -t %5
""",
)
def escape(
    target: Annotated[
        str, typer.Option("-t", "--target", help="Target pane", show_default=True)
    ] = "",
) -> None:
    """Send Escape key to pane.

    Useful for exiting vim insert mode, canceling prompts,
    or clearing partial input.

    JSON: {"ok": true, "escaped": true}
    Exit: Always 0.
    """
    pane = get_pane(target)
    pane.send_keys("Escape", enter=False)

    output_result({"escaped": True})


@app.command(
    rich_help_panel="Info",
    epilog="Use --all for all agent sockets. Use 'twmux --force status --all' for ALL sockets.",
)
def status(
    all_sockets: Annotated[
        bool, typer.Option("--all", help="Show all agent sockets (claude*)")
    ] = False,
) -> None:
    """Show tmux sessions, windows, and panes.

    Lists hierarchy with pane IDs (%N format) for use with -t flag.
    Default shows only the agent socket (claude).

    JSON: {"ok": true, "sockets": [{socket, sessions: [...]}]}
    Exit: Always 0.
    """
    from libtmux import Server

    from twmux.lib.safety import enumerate_agent_sockets, enumerate_all_sockets

    # Determine which sockets to show
    if all_sockets:
        if force_socket:
            sockets_to_show = enumerate_all_sockets()
        else:
            sockets_to_show = enumerate_agent_sockets()
    else:
        sockets_to_show = [socket_name]

    all_data = []

    for sock in sockets_to_show:
        try:
            server = Server(socket_name=sock)
            if not server.sessions:
                continue
        except Exception:
            continue

        sessions_data = []
        for session in server.sessions:
            windows_data = []
            for window in session.windows:
                panes_data = [
                    {"pane_id": p.pane_id, "pane_index": p.pane_index} for p in window.panes
                ]
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

        all_data.append(
            {
                "socket": sock,
                "sessions": sessions_data,
            }
        )

    if json_output:
        print(json_lib.dumps({"ok": True, "sockets": all_data}, indent=2))
    else:
        if not all_data:
            rprint(f'No tmux server running on socket "{socket_name}".')
            rprint('Use "twmux new <session>" to create one.')
            return

        for socket_data in all_data:
            sock = socket_data["socket"]
            rprint(f"[bold cyan]\\[{sock}][/bold cyan]")
            for s in socket_data["sessions"]:
                rprint(f"  [bold]{s['session_name']}[/bold] ({s['session_id']})")
                for w in s["windows"]:
                    rprint(f"    {w['window_index']}: {w['window_name']} ({w['window_id']})")
                    for p in w["panes"]:
                        rprint(f"      .{p['pane_index']}: {p['pane_id']}")


@app.command(
    rich_help_panel="Session Management",
    epilog="""
[bold]Examples[/bold]

twmux new myapp                    # Create session "myapp"

twmux new myapp -c "python3"       # Create and run command

twmux -L claude-isolated new test  # Use different socket
""",
)
def new(
    session_name: Annotated[str, typer.Argument(help="Name for the new session")],
    command: Annotated[
        str | None, typer.Option("-c", "--command", help="Command to run in pane")
    ] = None,
) -> None:
    """Create new tmux session on agent socket.

    Creates a session on the default socket (claude) or specified socket.
    Prints monitor command for user to attach and observe.

    JSON: {"ok": true, "session": str, "socket": str, "pane_id": str, "monitor_cmd": str}
    Exit: 0 success, 1 if session already exists.
    """
    from libtmux import Server
    from libtmux.exc import LibTmuxException

    server = Server(socket_name=socket_name)

    # Check if session already exists
    existing = [s for s in server.sessions if s.session_name == session_name]
    if existing:
        error_result(f"Session '{session_name}' already exists on socket '{socket_name}'")
        raise typer.Exit(1)

    # Create session
    try:
        session = server.new_session(session_name=session_name)
    except LibTmuxException as e:
        error_result(str(e))
        raise typer.Exit(1)

    pane = session.active_window.active_pane

    # Run command if specified
    if command:
        pane.send_keys(command, enter=True)

    monitor_cmd = f"tmux -L {socket_name} attach -t {session_name}"

    if json_output:
        output_result(
            {
                "session": session_name,
                "socket": socket_name,
                "pane_id": pane.pane_id,
                "monitor_cmd": monitor_cmd,
            }
        )
    else:
        rprint(f"Session created: [bold]{session_name}[/bold] on socket [bold]{socket_name}[/bold]")
        rprint(f"Pane ID: {pane.pane_id}")
        rprint()
        rprint(f"To monitor:  [cyan]{monitor_cmd}[/cyan]")
        rprint("To detach:   [cyan]Ctrl+b d[/cyan]")


@app.command(
    name="kill-session",
    rich_help_panel="Session Management",
    epilog="""
[bold]Example[/bold]
  twmux kill-session myapp
""",
)
def kill_session_cmd(
    session_name: Annotated[str, typer.Argument(help="Session to kill")],
) -> None:
    """Kill a tmux session.

    Removes the specified session from the socket.

    JSON: {"ok": true, "killed": true, "socket": str, "session": str}
    Exit: 0 success, 1 if session not found.
    """
    from libtmux import Server

    server = Server(socket_name=socket_name)

    # Find session
    sessions = [s for s in server.sessions if s.session_name == session_name]
    if not sessions:
        error_result(f"Session '{session_name}' not found on socket '{socket_name}'")
        raise typer.Exit(1)

    sessions[0].kill()

    if json_output:
        output_result(
            {
                "killed": True,
                "socket": socket_name,
                "session": session_name,
            }
        )
    else:
        rprint(f"Killed session [bold]{session_name}[/bold] on socket [bold]{socket_name}[/bold]")


@app.command(
    name="kill-server",
    rich_help_panel="Session Management",
    epilog="""
[bold]Examples[/bold]

twmux kill-server                    # Kill default claude server

twmux -L claude-isolated kill-server # Kill specific socket
""",
)
def kill_server_cmd() -> None:
    """Kill entire tmux server for socket.

    Terminates the tmux server and all its sessions.
    Only operates on agent sockets (claude*) unless --force is used.

    JSON: {"ok": true, "killed": true, "socket": str}
    Exit: 0 success, 1 if no server running.
    """
    from libtmux import Server

    server = Server(socket_name=socket_name)

    if not server.sessions:
        error_result(f"No server running on socket '{socket_name}'")
        raise typer.Exit(1)

    server.kill()

    if json_output:
        output_result(
            {
                "killed": True,
                "socket": socket_name,
            }
        )
    else:
        rprint(f"Killed server on socket [bold]{socket_name}[/bold]")


@app.command(
    name="move-window",
    rich_help_panel="Session Management",
    epilog="""
[bold]Examples[/bold]

twmux move-window -t build:0 debug       # Move window 0 of "build"

twmux move-window -t %5 debug            # Move window containing %5
""",
)
def move_window(
    destination: Annotated[str, typer.Argument(help="Destination session name")],
    target: Annotated[
        str, typer.Option("-t", "--target", help="Source pane in window to move", show_default=True)
    ] = "",
) -> None:
    """Move window to another session.

    Moves the entire window (with all panes) to the destination session.
    Target identifies a pane in the window to move.

    JSON: {"ok": true, "window_id": str, "window_index": str,
           "pane_ids": [str], "destination_session": str}
    Exit: 0 success, 1 if same-session or destination not found.
    """
    pane = get_pane(target)
    window = pane.window

    dest_session, _window_spec = resolve_destination(pane.server, destination)

    window.move_window(session=dest_session.session_id)

    output_result(
        {
            "window_id": window.window_id,
            "window_index": window.window_index,
            "pane_ids": [p.pane_id for p in window.panes],
            "destination_session": dest_session.session_name,
        }
    )


if __name__ == "__main__":
    app()

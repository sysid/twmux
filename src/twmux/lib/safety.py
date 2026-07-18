"""Socket safety validation and enumeration for agent isolation."""

from __future__ import annotations

import os
from pathlib import Path

# Default socket for all agent operations
DEFAULT_SOCKET = "claude"


class SocketValidationError(Exception):
    """Raised when socket validation fails."""

    pass


def is_agent_socket(socket_name: str) -> bool:
    """Check if socket name is an agent socket (starts with 'claude')."""
    return socket_name.startswith("claude")


def validate_socket(socket_name: str, force: bool) -> None:
    """Validate socket access, raise if non-agent socket without force.

    Args:
        socket_name: tmux socket name
        force: if True, allow non-agent sockets

    Raises:
        SocketValidationError: if non-agent socket and force=False
    """
    if not is_agent_socket(socket_name) and not force:
        raise SocketValidationError(
            f'Socket "{socket_name}" is not an agent socket (claude*).\nUse --force to override.'
        )


def get_socket_dir() -> Path:
    """Get the tmux socket directory for current user."""
    tmpdir = Path(os.getenv("TMUX_TMPDIR", "/tmp"))
    return tmpdir / f"tmux-{os.geteuid()}"


def enumerate_all_sockets() -> list[str]:
    """List all tmux socket names for current user.

    Returns:
        List of socket names (not paths). Empty if directory doesn't exist.
    """
    socket_dir = get_socket_dir()
    if not socket_dir.exists():
        return []

    return [f.name for f in socket_dir.iterdir() if f.is_socket() or f.exists()]


def enumerate_agent_sockets() -> list[str]:
    """List all agent tmux sockets (claude*) for current user.

    Returns:
        List of agent socket names.
    """
    return [name for name in enumerate_all_sockets() if is_agent_socket(name)]


def _current_tty() -> str | None:
    """Return the controlling terminal's path, or None if there isn't one.

    Works under `env -i` because the controlling terminal is a kernel attribute,
    not an environment variable.
    """
    try:
        fd = os.open("/dev/tty", os.O_RDONLY)
    except OSError:
        return None
    try:
        return os.ttyname(fd)
    finally:
        os.close(fd)


def recover_tmux_env(tty: str | None = None, socket_name: str | None = None) -> dict | None:
    """Find the tmux pane attached to `tty` and return data to rebuild TMUX env.

    Use case: a shell scrubbed by `env -i` has lost `TMUX` / `TMUX_PANE`, but is
    still attached to the same pane's tty. Walk live tmux sockets, find the pane
    whose `pane_tty` matches, and return what's needed to reconstruct the env.

    This is read-only introspection and deliberately bypasses agent-socket
    validation — the caller wants their *own* pane, wherever it happens to live.

    Args:
        tty: tty path (e.g. "/dev/ttys001"). If None, uses /dev/tty of caller.
        socket_name: limit search to a single socket. If None, scans every live
            socket in `get_socket_dir()`.

    Returns:
        dict with keys: socket, socket_path, server_pid, session_id, pane_id,
        tty, tmux, tmux_pane — or None if no match.
    """
    from libtmux import Server

    if tty is None:
        tty = _current_tty()
        if tty is None:
            return None

    socket_dir = get_socket_dir()
    if not socket_dir.exists():
        return None

    sockets = [socket_name] if socket_name else enumerate_all_sockets()

    for sock in sockets:
        try:
            server = Server(socket_name=sock)
            sessions = list(server.sessions)
        except Exception:
            continue
        if not sessions:
            continue

        matched_session = None
        matched_pane = None
        for session in sessions:
            for window in session.windows:
                for pane in window.panes:
                    if pane.pane_tty == tty:
                        matched_session = session
                        matched_pane = pane
                        break
                if matched_pane:
                    break
            if matched_pane:
                break

        if matched_pane is None or matched_session is None:
            continue

        try:
            pid_result = server.cmd("display-message", "-pF#{pid}")
            server_pid = int(pid_result.stdout[0])
        except Exception:
            continue

        session_id = matched_session.session_id
        pane_id = matched_pane.pane_id
        if session_id is None or pane_id is None:
            continue

        session_id_num = session_id.lstrip("$")
        socket_path = str(socket_dir / sock)
        return {
            "socket": sock,
            "socket_path": socket_path,
            "server_pid": server_pid,
            "session_id": session_id_num,
            "pane_id": pane_id,
            "tty": tty,
            "tmux": f"{socket_path},{server_pid},{session_id_num}",
            "tmux_pane": pane_id,
        }

    return None

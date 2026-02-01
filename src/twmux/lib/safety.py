"""Socket safety validation and enumeration for agent isolation."""

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

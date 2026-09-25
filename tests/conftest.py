"""Shared pytest fixtures using libtmux."""

import itertools
import os
import time

import pytest
from libtmux import Server

from twmux.lib.safety import get_socket_dir

# libtmux pytest plugin provides: server, session fixtures automatically.
# We override `config_file` and `server` to make test servers hermetic, and
# add a `pane` fixture that is ready for input.

# Every pane runs this instead of the user's login shell. The user's shell
# profile plus tmux.conf made a fresh pane take ~1s to reach its prompt —
# longer than send_safe's Enter-retry window, so early sends flaked. PS1 is a
# fixed "$ " so readiness is a deterministic check; PROMPT_COMMAND is dropped
# because it references hooks (direnv) that --norc never defines.
TEST_SHELL = "env -u PROMPT_COMMAND PS1='$ ' bash --noprofile --norc"

# PID-scoped so a server leaked by a crashed earlier run is never reused.
_socket_ids = itertools.count()


@pytest.fixture(scope="session")
def config_file(tmp_path_factory):
    """Minimal tmux.conf: keeps the user's config out of test servers."""
    path = tmp_path_factory.mktemp("tmux") / "tmux.conf"
    path.write_text(f'set -g base-index 1\nset -g default-command "{TEST_SHELL}"\n')
    return path


def kill_server(server):
    """Kill a test server and remove its socket file.

    tmux on macOS leaves the socket file behind after kill-server, so each
    test would otherwise add a dead file to the user's socket directory.
    """
    server.kill()
    (get_socket_dir() / server.socket_name).unlink(missing_ok=True)


@pytest.fixture
def server(request, config_file):
    """Temporary tmux server started with the test config.

    The plugin's own `server` fixture never passes config_file, so tmux would
    load the user's ~/.config/tmux/tmux.conf.
    """
    server = Server(
        socket_name=f"twmux_test_{os.getpid()}_{next(_socket_ids)}", config_file=str(config_file)
    )
    request.addfinalizer(lambda: kill_server(server))
    return server


@pytest.fixture
def tmux_server(request, config_file):
    """Factory for extra running servers on named sockets: tmux_server(prefix).

    For tests that need socket names with meaning — agent sockets must start
    with "claude" — without ever touching the user's real sockets (tests used
    to create and kill the live `claude` agent socket). Each server gets a
    PID-scoped name, the test config and a "placeholder" session (a tmux
    server exits without one), and is killed even if the test fails.
    """
    servers = []

    def start(prefix):
        server = Server(
            socket_name=f"{prefix}-{os.getpid()}-{next(_socket_ids)}",
            config_file=str(config_file),
        )
        server.new_session(session_name="placeholder")
        servers.append(server)
        return server

    request.addfinalizer(lambda: [kill_server(server) for server in servers])
    return start


@pytest.fixture
def pane(session):
    """Get the active pane from test session, once its shell reads input."""
    pane = session.active_window.active_pane
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if pane.capture_pane()[-1:] == ["$"]:
            return pane
        time.sleep(0.01)
    pytest.fail(f"shell in {pane.pane_id} never showed its prompt: {pane.capture_pane()}")

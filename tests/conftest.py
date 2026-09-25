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
_socket_names = (f"twmux_test_{os.getpid()}_{i}" for i in itertools.count())


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
    server = Server(socket_name=next(_socket_names), config_file=str(config_file))
    request.addfinalizer(lambda: kill_server(server))
    return server


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

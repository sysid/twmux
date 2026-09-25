"""Contract of the shared tmux fixtures in conftest.py."""


def test_pane_is_at_shell_prompt_when_handed_to_test(pane):
    """Regression: tests used to send into a pane whose shell was still
    starting (user tmux.conf + login profile ≈ 1s), outlasting send_safe's
    ~0.8s Enter-retry window — flaky "success: False, attempts: 3". The
    fixture must hand over a pane that is already reading input."""
    assert pane.capture_pane()[-1] == "$"


def test_test_server_ignores_user_tmux_config(server, session):
    """The user's tmux.conf (prefix, plugins, run-shell hooks) must not leak
    into test servers — it slows startup and makes tests machine-dependent."""
    prefix = server.cmd("show-options", "-g", "prefix").stdout

    assert prefix == ["prefix C-b"]


def test_kill_server_removes_its_socket_file(server):
    """tmux on macOS leaves the socket file behind after kill-server; every
    test run used to add one dead file per test to the socket directory."""
    from tests.conftest import kill_server
    from twmux.lib.safety import get_socket_dir

    server.new_session(session_name="probe")
    socket_file = get_socket_dir() / server.socket_name
    assert socket_file.exists()

    kill_server(server)

    assert not socket_file.exists()

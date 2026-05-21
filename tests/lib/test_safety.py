"""Tests for safety module."""

import os

import pytest

from twmux.lib.safety import (
    DEFAULT_SOCKET,
    SocketValidationError,
    enumerate_agent_sockets,
    enumerate_all_sockets,
    get_socket_dir,
    is_agent_socket,
    recover_tmux_env,
    validate_socket,
)


class TestConstants:
    def test_default_socket_is_claude(self):
        assert DEFAULT_SOCKET == "claude"


class TestIsAgentSocket:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("claude", True),
            ("claude-isolated", True),
            ("claude-test-123", True),
            ("default", False),
            ("my-project", False),
            ("", False),
            ("CLAUDE", False),  # case-sensitive
        ],
    )
    def test_is_agent_socket(self, name, expected):
        assert is_agent_socket(name) == expected


class TestValidateSocket:
    def test_agent_socket_without_force_passes(self):
        # Should not raise
        validate_socket("claude", force=False)
        validate_socket("claude-test", force=False)

    def test_non_agent_socket_without_force_raises(self):
        with pytest.raises(SocketValidationError) as exc_info:
            validate_socket("default", force=False)
        assert "default" in str(exc_info.value)
        assert "not an agent socket" in str(exc_info.value)

    def test_non_agent_socket_with_force_passes(self):
        # Should not raise
        validate_socket("default", force=True)
        validate_socket("my-project", force=True)

    def test_agent_socket_with_force_passes(self):
        # Force has no effect on agent sockets
        validate_socket("claude", force=True)


class TestGetSocketDir:
    def test_returns_path_with_uid(self):
        socket_dir = get_socket_dir()
        assert f"tmux-{os.geteuid()}" in str(socket_dir)

    def test_respects_tmux_tmpdir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
        socket_dir = get_socket_dir()
        assert str(tmp_path) in str(socket_dir)


class TestEnumerateSockets:
    def test_enumerate_all_returns_list(self):
        # May be empty if no tmux running
        result = enumerate_all_sockets()
        assert isinstance(result, list)

    def test_enumerate_agent_filters_correctly(self, monkeypatch, tmp_path):
        # Create fake socket directory
        socket_dir = tmp_path / f"tmux-{os.geteuid()}"
        socket_dir.mkdir(parents=True)

        # Create fake socket files
        (socket_dir / "claude").touch()
        (socket_dir / "claude-test").touch()
        (socket_dir / "default").touch()
        (socket_dir / "myproject").touch()

        monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))

        agent_sockets = enumerate_agent_sockets()
        assert set(agent_sockets) == {"claude", "claude-test"}

        all_sockets = enumerate_all_sockets()
        assert set(all_sockets) == {"claude", "claude-test", "default", "myproject"}


class TestRecoverTmuxEnv:
    def test_no_socket_dir_returns_none(self, monkeypatch, tmp_path):
        # Point TMUX_TMPDIR at a directory where no tmux-UID/ exists.
        monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
        assert recover_tmux_env(tty="/dev/null") is None

    def test_matches_pane_by_tty(self, pane):
        """Real libtmux pane → recover finds it via its actual tty."""
        sock = pane.server.socket_name
        result = recover_tmux_env(tty=pane.pane_tty, socket_name=sock)

        assert result is not None
        assert result["pane_id"] == pane.pane_id
        assert result["tty"] == pane.pane_tty
        assert result["socket"] == sock
        assert result["server_pid"] > 0
        # session_id is the numeric portion, no leading "$"
        assert not result["session_id"].startswith("$")
        # tmux field is the eval-able TMUX value
        expected_prefix = result["socket_path"]
        assert result["tmux"].startswith(expected_prefix + ",")
        assert result["tmux_pane"] == pane.pane_id

    def test_no_matching_tty_returns_none(self, pane):
        """A tty that doesn't exist on any socket returns None."""
        sock = pane.server.socket_name
        assert recover_tmux_env(tty="/dev/nonexistent-tty-xyz", socket_name=sock) is None

    def test_socket_name_filter_skips_others(self, pane):
        """Passing a wrong socket_name should not match even if tty is right."""
        result = recover_tmux_env(tty=pane.pane_tty, socket_name="definitely-not-a-socket")
        assert result is None


class TestRecoverTmuxEnvMocked:
    """Mock-based tests that don't need fork() — exercise the matching logic
    without spawning a real tmux server. Complements the libtmux-fixture tests
    above (which need a real fork-capable environment)."""

    def _fake_server(self, panes_by_session, server_pid=4242):
        """Build a stand-in for libtmux.Server.

        panes_by_session: list[(session_id, [(pane_id, pane_tty), ...])]
        """
        from types import SimpleNamespace

        sessions = []
        for sid, pane_specs in panes_by_session:
            panes = [SimpleNamespace(pane_id=pid, pane_tty=tty) for pid, tty in pane_specs]
            window = SimpleNamespace(panes=panes)
            sessions.append(SimpleNamespace(session_id=sid, windows=[window]))

        cmd_result = SimpleNamespace(stdout=[str(server_pid)], stderr=[], returncode=0)
        return SimpleNamespace(sessions=sessions, cmd=lambda *a, **kw: cmd_result)

    def test_match_constructs_expected_envelope(self, monkeypatch, tmp_path):
        socket_dir = tmp_path / f"tmux-{os.geteuid()}"
        socket_dir.mkdir(parents=True)
        (socket_dir / "default").touch()
        monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))

        fake = self._fake_server(
            panes_by_session=[("$3", [("%1", "/dev/ttys000"), ("%2", "/dev/ttys001")])],
            server_pid=4242,
        )
        import libtmux
        monkeypatch.setattr(libtmux, "Server", lambda **kw: fake)

        result = recover_tmux_env(tty="/dev/ttys001")
        assert result is not None
        assert result["pane_id"] == "%2"
        assert result["server_pid"] == 4242
        assert result["session_id"] == "3"  # leading "$" stripped
        assert result["socket"] == "default"
        assert result["tmux"] == f"{result['socket_path']},4242,3"
        assert result["tmux_pane"] == "%2"

    def test_no_match_returns_none(self, monkeypatch, tmp_path):
        socket_dir = tmp_path / f"tmux-{os.geteuid()}"
        socket_dir.mkdir(parents=True)
        (socket_dir / "default").touch()
        monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))

        fake = self._fake_server(panes_by_session=[("$0", [("%1", "/dev/ttys000")])])
        import libtmux
        monkeypatch.setattr(libtmux, "Server", lambda **kw: fake)

        assert recover_tmux_env(tty="/dev/ttys999") is None

    def test_dead_socket_is_skipped(self, monkeypatch, tmp_path):
        """A socket whose Server() raises should not abort the scan."""
        socket_dir = tmp_path / f"tmux-{os.geteuid()}"
        socket_dir.mkdir(parents=True)
        (socket_dir / "dead").touch()
        (socket_dir / "live").touch()
        monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))

        fake_live = self._fake_server(panes_by_session=[("$5", [("%7", "/dev/ttys042")])])

        def server_factory(socket_name=None, **kw):
            if socket_name == "dead":
                raise RuntimeError("server gone")
            return fake_live

        import libtmux
        monkeypatch.setattr(libtmux, "Server", server_factory)

        result = recover_tmux_env(tty="/dev/ttys042")
        assert result is not None
        assert result["pane_id"] == "%7"

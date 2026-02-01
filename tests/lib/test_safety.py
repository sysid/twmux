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

"""Tests for CLI."""

import json
import time

from typer.testing import CliRunner

from twmux.bin.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "twmux" in result.output


def test_send_command(pane):
    """Send command via CLI."""
    pane_id = pane.pane_id
    socket_name = pane.server.socket_name

    result = runner.invoke(
        app, ["-L", socket_name, "--force", "send", "-t", pane_id, "echo cli_test"]
    )
    assert result.exit_code == 0, f"Failed: {result.output}"

    time.sleep(0.3)
    content = pane.capture_pane()
    assert any("cli_test" in line for line in content)


def test_send_command_json(pane):
    """Send command with JSON output."""
    pane_id = pane.pane_id
    socket_name = pane.server.socket_name

    result = runner.invoke(
        app, ["-L", socket_name, "--force", "--json", "send", "-t", pane_id, "echo json_test"]
    )
    assert result.exit_code == 0, f"Failed: {result.output}"

    data = json.loads(result.output)
    assert data["success"] is True
    assert data["attempts"] >= 1


def test_exec_command(pane):
    """Execute command via CLI."""
    pane_id = pane.pane_id
    socket_name = pane.server.socket_name
    time.sleep(0.3)

    result = runner.invoke(
        app, ["-L", socket_name, "--force", "--json", "exec", "-t", pane_id, "echo exec_test"]
    )
    assert result.exit_code == 0, f"Failed: {result.output}"

    data = json.loads(result.output)
    assert data["exit_code"] == 0
    assert "exec_test" in data["output"]


def test_exec_command_failure(pane):
    """Execute failing command via CLI."""
    pane_id = pane.pane_id
    socket_name = pane.server.socket_name
    time.sleep(0.3)

    result = runner.invoke(
        app, ["-L", socket_name, "--force", "--json", "exec", "-t", pane_id, "ls /nonexistent_xyz"]
    )
    # CLI should succeed, but exit_code in result should be non-zero
    assert result.exit_code == 0, f"Failed: {result.output}"

    data = json.loads(result.output)
    assert data["exit_code"] != 0


def test_capture_command(pane):
    """Capture pane content via CLI."""
    pane_id = pane.pane_id
    socket_name = pane.server.socket_name

    # Send something to capture
    pane.send_keys("echo capture_marker", enter=True)
    time.sleep(0.3)

    result = runner.invoke(app, ["-L", socket_name, "--force", "capture", "-t", pane_id])
    assert result.exit_code == 0, f"Failed: {result.output}"
    assert "capture_marker" in result.output


def test_wait_idle_command(pane):
    """Wait for idle via CLI."""
    pane_id = pane.pane_id
    socket_name = pane.server.socket_name
    time.sleep(0.3)

    result = runner.invoke(
        app, ["-L", socket_name, "--force", "--json", "wait-idle", "-t", pane_id, "--timeout", "2"]
    )
    assert result.exit_code == 0, f"Failed: {result.output}"

    data = json.loads(result.output)
    assert data["idle"] is True


def test_interrupt_command(pane):
    """Send Ctrl+C via CLI."""
    pane_id = pane.pane_id
    socket_name = pane.server.socket_name

    # Start a long-running command
    pane.send_keys("sleep 100", enter=True)
    time.sleep(0.2)

    result = runner.invoke(
        app, ["-L", socket_name, "--force", "--json", "interrupt", "-t", pane_id]
    )
    assert result.exit_code == 0, f"Failed: {result.output}"

    # Verify the interrupt was sent
    data = json.loads(result.output)
    assert data["interrupted"] is True


def test_launch_command(session):
    """Launch new pane via CLI."""
    socket_name = session.server.socket_name
    pane = session.active_window.active_pane
    pane_id = pane.pane_id
    initial_pane_count = len(session.active_window.panes)

    result = runner.invoke(app, ["-L", socket_name, "--force", "--json", "launch", "-t", pane_id])
    assert result.exit_code == 0, f"Failed: {result.output}"

    data = json.loads(result.output)
    assert "pane_id" in data

    # Verify new pane created
    assert len(session.active_window.panes) == initial_pane_count + 1


def test_kill_command(session):
    """Kill pane via CLI."""
    socket_name = session.server.socket_name

    # Create a pane to kill
    new_pane = session.active_window.active_pane.split()
    pane_id = new_pane.pane_id

    initial_count = len(session.active_window.panes)

    result = runner.invoke(app, ["-L", socket_name, "--force", "kill", "-t", pane_id])
    assert result.exit_code == 0, f"Failed: {result.output}"

    # Verify pane was killed
    assert len(session.active_window.panes) == initial_count - 1


def test_escape_command(pane):
    """Send Escape via CLI."""
    pane_id = pane.pane_id
    socket_name = pane.server.socket_name

    result = runner.invoke(app, ["-L", socket_name, "--force", "--json", "escape", "-t", pane_id])
    assert result.exit_code == 0, f"Failed: {result.output}"

    data = json.loads(result.output)
    assert data["escaped"] is True


def test_status_command(session):
    """Show tmux status via CLI."""
    socket_name = session.server.socket_name

    result = runner.invoke(app, ["-L", socket_name, "--force", "--json", "status"])
    assert result.exit_code == 0, f"Failed: {result.output}"

    data = json.loads(result.output)
    assert "sockets" in data
    assert len(data["sockets"]) > 0
    assert data["sockets"][0]["socket"] == socket_name
    assert len(data["sockets"][0]["sessions"]) > 0


class TestSocketSafety:
    """Test socket safety enforcement."""

    def test_non_agent_socket_requires_force(self):
        """Non-claude sockets require --force."""
        result = runner.invoke(app, ["-L", "default", "status"])
        assert result.exit_code != 0
        assert "not an agent socket" in result.output

    def test_non_agent_socket_with_force_works(self, session):
        """--force allows non-claude sockets."""
        socket_name = session.server.socket_name

        result = runner.invoke(app, ["-L", socket_name, "--force", "status"])
        assert result.exit_code == 0

    def test_agent_socket_works_without_force(self):
        """claude* sockets work without --force."""
        from libtmux import Server

        server = Server(socket_name="claude-test-safety")
        server.new_session("test-session")
        try:
            result = runner.invoke(app, ["-L", "claude-test-safety", "status"])
            assert result.exit_code == 0
        finally:
            server.kill()


class TestNewCommand:
    """Test twmux new command."""

    def test_new_creates_session(self):
        """new command creates a session."""
        from libtmux import Server

        result = runner.invoke(app, ["-L", "claude-test-new", "--json", "new", "test-session"])
        assert result.exit_code == 0, f"Failed: {result.output}"

        data = json.loads(result.output)
        assert data["session"] == "test-session"
        assert data["socket"] == "claude-test-new"
        assert "pane_id" in data
        assert "monitor_cmd" in data
        assert "tmux -L claude-test-new attach" in data["monitor_cmd"]

        # Cleanup
        server = Server(socket_name="claude-test-new")
        server.kill()

    def test_new_with_command(self):
        """new command with -c runs command in pane."""
        from libtmux import Server

        result = runner.invoke(
            app,
            ["-L", "claude-test-new", "--json", "new", "cmd-session", "-c", "echo hello"],
        )
        assert result.exit_code == 0, f"Failed: {result.output}"

        # Cleanup
        server = Server(socket_name="claude-test-new")
        server.kill()

    def test_new_duplicate_session_fails(self):
        """new command fails if session already exists."""
        from libtmux import Server

        # Create session first
        result1 = runner.invoke(app, ["-L", "claude-test-dup", "new", "dup-session"])
        assert result1.exit_code == 0

        # Try to create again
        result2 = runner.invoke(app, ["-L", "claude-test-dup", "new", "dup-session"])
        assert result2.exit_code == 1

        # Cleanup
        server = Server(socket_name="claude-test-dup")
        server.kill()

    def test_new_human_output_shows_monitor_command(self):
        """new command shows monitor instructions in human mode."""
        from libtmux import Server

        result = runner.invoke(app, ["-L", "claude-test-human", "new", "human-session"])
        assert result.exit_code == 0, f"Failed: {result.output}"

        assert "To monitor:" in result.output
        assert "tmux -L claude-test-human attach" in result.output
        assert "Ctrl+b d" in result.output

        # Cleanup
        server = Server(socket_name="claude-test-human")
        server.kill()


class TestKillSessionCommand:
    """Test twmux kill-session command."""

    def test_kill_session_removes_session(self):
        """kill-session removes the specified session."""
        from libtmux import Server

        # Create session first
        runner.invoke(app, ["-L", "claude-test-kill", "new", "to-kill"])

        # Kill it
        result = runner.invoke(app, ["-L", "claude-test-kill", "--json", "kill-session", "to-kill"])
        assert result.exit_code == 0, f"Failed: {result.output}"

        data = json.loads(result.output)
        assert data["killed"] is True
        assert data["session"] == "to-kill"

        # Cleanup server (no sessions left)
        server = Server(socket_name="claude-test-kill")
        server.kill()

    def test_kill_session_nonexistent_fails(self):
        """kill-session fails if session doesn't exist."""
        from libtmux import Server

        # Create a different session so server exists
        server = Server(socket_name="claude-test-kill2")
        server.new_session("other")

        result = runner.invoke(app, ["-L", "claude-test-kill2", "kill-session", "nonexistent"])
        assert result.exit_code == 1

        server.kill()


class TestKillServerCommand:
    """Test twmux kill-server command."""

    def test_kill_server_removes_server(self):
        """kill-server kills the entire tmux server."""
        from libtmux import Server

        # Create server with sessions
        runner.invoke(app, ["-L", "claude-test-killsrv", "new", "s1"])
        runner.invoke(app, ["-L", "claude-test-killsrv", "new", "s2"])

        # Kill server
        result = runner.invoke(app, ["-L", "claude-test-killsrv", "--json", "kill-server"])
        assert result.exit_code == 0, f"Failed: {result.output}"

        data = json.loads(result.output)
        assert data["killed"] is True

        # Verify server is gone
        server = Server(socket_name="claude-test-killsrv")
        assert len(server.sessions) == 0

    def test_kill_server_refuses_non_agent_socket(self):
        """kill-server refuses to kill non-agent sockets."""
        result = runner.invoke(app, ["-L", "default", "kill-server"])
        assert result.exit_code == 1
        assert "not an agent socket" in result.output

    def test_kill_server_non_agent_with_force(self):
        """kill-server with --force works on non-agent sockets."""
        from libtmux import Server

        # Create a non-agent server
        server = Server(socket_name="test-non-agent")
        server.new_session("test")

        result = runner.invoke(app, ["-L", "test-non-agent", "--force", "--json", "kill-server"])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert data["killed"] is True


class TestMovePaneCommand:
    """Test twmux move-pane command."""

    def test_move_pane_creates_new_window(self):
        """T001: move-pane creates new window in destination session."""
        from libtmux import Server

        server = Server(socket_name="claude-test-mvpane")
        try:
            src_session = server.new_session("source")
            server.new_session("dest")

            # Split to have 2 panes so source session survives the move
            src_pane = src_session.active_window.active_pane.split()
            pane_id = src_pane.pane_id

            args = [
                "-L",
                "claude-test-mvpane",
                "--force",
                "--json",
                "move-pane",
                "-t",
                pane_id,
                "dest",
            ]
            result = runner.invoke(app, args)
            assert result.exit_code == 0, f"Failed: {result.output}"

            data = json.loads(result.output)
            assert data["pane_id"] == pane_id
            assert data["destination_session"] == "dest"
            assert data["new_window"] is True

            # Verify pane is in destination session
            dst_session = [s for s in server.sessions if s.session_name == "dest"][0]
            dst_pane_ids = [p.pane_id for w in dst_session.windows for p in w.panes]
            assert pane_id in dst_pane_ids

            # Verify pane no longer in source session
            src_session_fresh = [s for s in server.sessions if s.session_name == "source"][0]
            src_pane_ids = [p.pane_id for w in src_session_fresh.windows for p in w.panes]
            assert pane_id not in src_pane_ids
        finally:
            server.kill()

    def test_move_pane_same_session_error(self):
        """T002: move-pane to same session returns error."""
        from libtmux import Server

        server = Server(socket_name="claude-test-mvpane2")
        try:
            server.new_session("source")
            pane = server.sessions[0].active_window.active_pane
            pane_id = pane.pane_id

            args = [
                "-L",
                "claude-test-mvpane2",
                "--force",
                "--json",
                "move-pane",
                "-t",
                pane_id,
                "source",
            ]
            result = runner.invoke(app, args)
            assert result.exit_code == 1

            data = json.loads(result.output)
            assert "error" in data
        finally:
            server.kill()

    def test_move_pane_destination_not_found(self):
        """T003: move-pane to non-existent session returns error."""
        from libtmux import Server

        server = Server(socket_name="claude-test-mvpane3")
        try:
            server.new_session("source")
            pane = server.sessions[0].active_window.active_pane
            pane_id = pane.pane_id

            args = [
                "-L",
                "claude-test-mvpane3",
                "--force",
                "--json",
                "move-pane",
                "-t",
                pane_id,
                "nonexistent",
            ]
            result = runner.invoke(app, args)
            assert result.exit_code == 1

            data = json.loads(result.output)
            assert "error" in data
        finally:
            server.kill()

    def test_move_pane_json_output_format(self):
        """T004: move-pane JSON contains required fields per contract."""
        from libtmux import Server

        server = Server(socket_name="claude-test-mvpane4")
        try:
            src_session = server.new_session("source")
            server.new_session("dest")

            # Need 2 panes so source survives
            src_pane = src_session.active_window.active_pane.split()
            pane_id = src_pane.pane_id

            args = [
                "-L",
                "claude-test-mvpane4",
                "--force",
                "--json",
                "move-pane",
                "-t",
                pane_id,
                "dest",
            ]
            result = runner.invoke(app, args)
            assert result.exit_code == 0, f"Failed: {result.output}"

            data = json.loads(result.output)
            # Verify types per cli-contract.md
            assert isinstance(data["pane_id"], str)
            assert isinstance(data["destination_session"], str)
            assert isinstance(data["new_window"], bool)
        finally:
            server.kill()

    def test_move_pane_join_existing_window(self):
        """T013: move-pane with session:window joins existing window."""
        from libtmux import Server

        server = Server(socket_name="claude-test-mvpane5")
        try:
            src_session = server.new_session("source")
            dst_session = server.new_session("dest")

            # Split source so it survives
            src_pane = src_session.active_window.active_pane.split()
            pane_id = src_pane.pane_id

            # Get actual window index (respects tmux base-index setting)
            dst_window = dst_session.windows[0]
            dst_win_idx = dst_window.window_index
            panes_before = len(dst_window.panes)

            result = runner.invoke(
                app,
                [
                    "-L",
                    "claude-test-mvpane5",
                    "--force",
                    "--json",
                    "move-pane",
                    "-t",
                    pane_id,
                    f"dest:{dst_win_idx}",
                ],
            )
            assert result.exit_code == 0, f"Failed: {result.output}"

            data = json.loads(result.output)
            assert data["pane_id"] == pane_id
            assert data["destination_session"] == "dest"
            assert data["new_window"] is False

            # Verify pane joined destination window (now has one more pane)
            dst_session_fresh = [s for s in server.sessions if s.session_name == "dest"][0]
            # Find the window by ID to avoid index confusion
            dst_window_fresh = [
                w for w in dst_session_fresh.windows if w.window_id == dst_window.window_id
            ][0]
            assert len(dst_window_fresh.panes) == panes_before + 1

            # Verify pane is in destination window
            dst_pane_ids = [p.pane_id for p in dst_window_fresh.panes]
            assert pane_id in dst_pane_ids
        finally:
            server.kill()


class TestMoveWindowCommand:
    """Test twmux move-window command."""

    def test_move_window_happy_path(self):
        """T008: move-window moves window with all panes to destination."""
        from libtmux import Server

        server = Server(socket_name="claude-test-mvwin")
        try:
            src_session = server.new_session("source")
            server.new_session("dest")

            # Create multi-pane window
            src_pane = src_session.active_window.active_pane
            new_pane = src_pane.split()
            window_pane_ids = [src_pane.pane_id, new_pane.pane_id]

            # Create a second window so source session survives
            src_session.new_window()

            result = runner.invoke(
                app,
                [
                    "-L",
                    "claude-test-mvwin",
                    "--force",
                    "--json",
                    "move-window",
                    "-t",
                    src_pane.pane_id,
                    "dest",
                ],
            )
            assert result.exit_code == 0, f"Failed: {result.output}"

            data = json.loads(result.output)
            assert "window_id" in data
            assert "window_index" in data
            assert "pane_ids" in data
            assert data["destination_session"] == "dest"

            # Verify all panes moved
            for pid in window_pane_ids:
                assert pid in data["pane_ids"]

            # Verify panes are in destination session
            dst_session = [s for s in server.sessions if s.session_name == "dest"][0]
            dst_pane_ids = [p.pane_id for w in dst_session.windows for p in w.panes]
            for pid in window_pane_ids:
                assert pid in dst_pane_ids
        finally:
            server.kill()

    def test_move_window_same_session_error(self):
        """T009: move-window to same session returns error."""
        from libtmux import Server

        server = Server(socket_name="claude-test-mvwin2")
        try:
            server.new_session("source")
            pane = server.sessions[0].active_window.active_pane
            pane_id = pane.pane_id

            result = runner.invoke(
                app,
                [
                    "-L",
                    "claude-test-mvwin2",
                    "--force",
                    "--json",
                    "move-window",
                    "-t",
                    pane_id,
                    "source",
                ],
            )
            assert result.exit_code == 1

            data = json.loads(result.output)
            assert "error" in data
        finally:
            server.kill()

    def test_move_window_json_output_format(self):
        """T010: move-window JSON contains required fields per contract."""
        from libtmux import Server

        server = Server(socket_name="claude-test-mvwin3")
        try:
            src_session = server.new_session("source")
            server.new_session("dest")

            src_pane = src_session.active_window.active_pane
            src_pane.split()

            # Create second window so source survives
            src_session.new_window()

            result = runner.invoke(
                app,
                [
                    "-L",
                    "claude-test-mvwin3",
                    "--force",
                    "--json",
                    "move-window",
                    "-t",
                    src_pane.pane_id,
                    "dest",
                ],
            )
            assert result.exit_code == 0, f"Failed: {result.output}"

            data = json.loads(result.output)
            # Verify types per cli-contract.md
            assert isinstance(data["window_id"], str)
            assert isinstance(data["window_index"], str)
            assert isinstance(data["pane_ids"], list)
            assert all(isinstance(pid, str) for pid in data["pane_ids"])
            assert isinstance(data["destination_session"], str)
        finally:
            server.kill()


class TestStatusEnhanced:
    """Test enhanced status command with --all flags."""

    def test_status_default_only_shows_claude(self):
        """Default status only shows the default socket."""
        from libtmux import Server

        # Create session on claude socket
        runner.invoke(app, ["new", "status-test"])

        result = runner.invoke(app, ["--json", "status"])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert "sockets" in data
        assert len(data["sockets"]) == 1
        assert data["sockets"][0]["socket"] == "claude"

        # Cleanup
        Server(socket_name="claude").kill()

    def test_status_all_shows_agent_sockets(self):
        """--all shows all claude* sockets."""
        from libtmux import Server

        # Create sessions on multiple agent sockets
        runner.invoke(app, ["-L", "claude", "new", "s1"])
        runner.invoke(app, ["-L", "claude-other", "new", "s2"])

        result = runner.invoke(app, ["--json", "status", "--all"])
        assert result.exit_code == 0

        data = json.loads(result.output)
        socket_names = [s["socket"] for s in data["sockets"]]
        assert "claude" in socket_names
        assert "claude-other" in socket_names

        # Cleanup
        Server(socket_name="claude").kill()
        Server(socket_name="claude-other").kill()

    def test_status_all_force_shows_everything(self):
        """--all --force shows all sockets including non-agent."""
        from libtmux import Server

        # Create sessions on agent and non-agent sockets
        runner.invoke(app, ["-L", "claude", "new", "agent"])

        non_agent = Server(socket_name="test-non-agent-status")
        non_agent.new_session("non-agent")

        result = runner.invoke(app, ["--json", "--force", "status", "--all"])
        assert result.exit_code == 0

        data = json.loads(result.output)
        socket_names = [s["socket"] for s in data["sockets"]]
        assert "claude" in socket_names
        assert "test-non-agent-status" in socket_names

        # Cleanup
        Server(socket_name="claude").kill()
        non_agent.kill()

    def test_status_all_without_force_excludes_non_agent(self):
        """--all without --force excludes non-agent sockets."""
        from libtmux import Server

        # Create both agent and non-agent
        runner.invoke(app, ["-L", "claude", "new", "agent"])

        non_agent = Server(socket_name="test-non-agent-excl")
        non_agent.new_session("non-agent")

        result = runner.invoke(app, ["--json", "status", "--all"])
        assert result.exit_code == 0

        data = json.loads(result.output)
        socket_names = [s["socket"] for s in data["sockets"]]
        assert "test-non-agent-excl" not in socket_names

        # Cleanup
        Server(socket_name="claude").kill()
        non_agent.kill()

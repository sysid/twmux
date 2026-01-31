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
    assert "0.1.0" in result.output


def test_send_command(pane):
    """Send command via CLI."""
    pane_id = pane.pane_id
    socket_name = pane.server.socket_name

    result = runner.invoke(app, ["-L", socket_name, "send", "-t", pane_id, "echo cli_test"])
    assert result.exit_code == 0, f"Failed: {result.output}"

    time.sleep(0.3)
    content = pane.capture_pane()
    assert any("cli_test" in line for line in content)


def test_send_command_json(pane):
    """Send command with JSON output."""
    pane_id = pane.pane_id
    socket_name = pane.server.socket_name

    result = runner.invoke(
        app, ["-L", socket_name, "--json", "send", "-t", pane_id, "echo json_test"]
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
        app, ["-L", socket_name, "--json", "exec", "-t", pane_id, "echo exec_test"]
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
        app, ["-L", socket_name, "--json", "exec", "-t", pane_id, "ls /nonexistent_xyz"]
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

    result = runner.invoke(app, ["-L", socket_name, "capture", "-t", pane_id])
    assert result.exit_code == 0, f"Failed: {result.output}"
    assert "capture_marker" in result.output


def test_wait_idle_command(pane):
    """Wait for idle via CLI."""
    pane_id = pane.pane_id
    socket_name = pane.server.socket_name
    time.sleep(0.3)

    result = runner.invoke(
        app, ["-L", socket_name, "--json", "wait-idle", "-t", pane_id, "--timeout", "2"]
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

    result = runner.invoke(app, ["-L", socket_name, "--json", "interrupt", "-t", pane_id])
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

    result = runner.invoke(app, ["-L", socket_name, "--json", "launch", "-t", pane_id])
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

    result = runner.invoke(app, ["-L", socket_name, "kill", "-t", pane_id])
    assert result.exit_code == 0, f"Failed: {result.output}"

    # Verify pane was killed
    assert len(session.active_window.panes) == initial_count - 1


def test_escape_command(pane):
    """Send Escape via CLI."""
    pane_id = pane.pane_id
    socket_name = pane.server.socket_name

    result = runner.invoke(app, ["-L", socket_name, "--json", "escape", "-t", pane_id])
    assert result.exit_code == 0, f"Failed: {result.output}"

    data = json.loads(result.output)
    assert data["escaped"] is True


def test_status_command(session):
    """Show tmux status via CLI."""
    socket_name = session.server.socket_name

    result = runner.invoke(app, ["-L", socket_name, "--json", "status"])
    assert result.exit_code == 0, f"Failed: {result.output}"

    data = json.loads(result.output)
    assert "sessions" in data
    assert len(data["sessions"]) > 0

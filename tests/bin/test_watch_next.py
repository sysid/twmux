"""Tests for `twmux watch next` against a hermetic tmux server with a client."""

import json
import subprocess
import time

import pytest
from typer.testing import CliRunner

from twmux.bin.cli import app
from twmux.lib import watch
from twmux.lib.safety import get_socket_dir

runner = CliRunner()


@pytest.fixture
def attached(session):
    """A control-mode client attached to `session` — switch-client needs a
    client to switch, and tests have no terminal."""
    client = subprocess.Popen(
        ["tmux", "-L", session.server.socket_name, "-C", "attach", "-t", session.session_name],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5.0
    while not session.server.cmd("list-clients").stdout:
        if time.monotonic() > deadline:
            pytest.fail("control client never attached")
        time.sleep(0.05)
    yield session
    client.terminate()
    client.wait()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "JSON_PATH", tmp_path / "agents.json")
    monkeypatch.setattr(watch, "NEXT_CURSOR_PATH", tmp_path / "next.json")
    return tmp_path


def tmux_env(server):
    return {"TMUX": f"{get_socket_dir() / server.socket_name},1,0", "TMUX_PANE": ""}


def client_session(server):
    return server.cmd("list-clients", "-F", "#{session_name}").stdout[0]


def write_agents(server, *rows):
    entries = [
        {
            "socket": server.socket_name,
            "pane_id": pane_id,
            "target": "",
            "project": "p",
            "agent": "claude_code",
            "state": state,
            "state_entered_at": entered_at,
            "title": "",
        }
        for pane_id, state, entered_at in rows
    ]
    watch.JSON_PATH.write_text(json.dumps(entries))


def test_next_switches_the_client_to_the_newest_done_agent(attached, cache):
    server = attached.server
    other = server.new_session(session_name="agents")
    agent_pane = other.active_window.active_pane.pane_id
    write_agents(server, (agent_pane, "done", 100.0))

    result = runner.invoke(app, ["watch", "next"], env=tmux_env(server))

    assert result.exit_code == 0, result.output
    assert result.output == ""  # run-shell would show any output in a view pane
    assert client_session(server) == "agents"


def test_next_json_reports_the_jump(attached, cache):
    server = attached.server
    other = server.new_session(session_name="agents")
    agent_pane = other.active_window.active_pane.pane_id
    write_agents(server, (agent_pane, "wait", 100.0))

    result = runner.invoke(app, ["--json", "watch", "next"], env=tmux_env(server))

    assert json.loads(result.output) == {
        "ok": True,
        "jumped": True,
        "pane_id": agent_pane,
        "state": "wait",
    }


def test_next_with_nothing_waiting_stays_put(attached, cache):
    server = attached.server
    write_agents(server)

    result = runner.invoke(app, ["--json", "watch", "next"], env=tmux_env(server))

    assert json.loads(result.output) == {"ok": True, "jumped": False}
    assert client_session(server) == attached.session_name


def test_next_without_daemon_output_fails_with_a_hint(attached, cache):
    result = runner.invoke(app, ["--json", "watch", "next"], env=tmux_env(attached.server))

    assert result.exit_code == 1
    assert "twmux watch daemon" in json.loads(result.output)["error"]


def test_next_outside_tmux_fails(cache):
    result = runner.invoke(app, ["--json", "watch", "next"], env={"TMUX": ""})

    assert result.exit_code == 1
    assert "not inside tmux" in json.loads(result.output)["error"]

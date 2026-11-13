"""Tests for `twmux mark` — the agent-hook entry point."""

import json

from typer.testing import CliRunner

from twmux.bin.cli import app
from twmux.lib.safety import get_socket_dir

runner = CliRunner()


def hook_env(pane, **extra):
    """The environment an agent hook inherits from the agent in `pane`."""
    socket_path = get_socket_dir() / pane.server.socket_name
    env = {"TMUX": f"{socket_path},1,0", "TMUX_PANE": pane.pane_id}
    env.update(extra)
    return env


def pane_option(pane, name):
    out = pane.cmd("show-options", "-p", "-v", "-t", pane.pane_id, name).stdout
    return out[0] if out else None


def test_mark_records_state_timestamp_and_command_on_the_hook_pane(pane):
    result = runner.invoke(app, ["mark", "working"], env=hook_env(pane))

    assert result.exit_code == 0
    assert pane_option(pane, "@twmux_state") == "working"
    assert int(pane_option(pane, "@twmux_state_ts")) > 1_700_000_000
    assert pane_option(pane, "@twmux_cmd") == "bash"


def test_mark_records_the_agent_name_when_given(pane):
    runner.invoke(app, ["mark", "wait", "--agent", "claude_code"], env=hook_env(pane))

    assert pane_option(pane, "@twmux_agent") == "claude_code"


def test_mark_prints_nothing_because_hook_stdout_reaches_the_agent(pane):
    """Claude Code adds UserPromptSubmit/SessionStart hook stdout to the
    model's context — a chatty mark would inject text into every prompt."""
    result = runner.invoke(app, ["mark", "done"], env=hook_env(pane))

    assert result.exit_code == 0
    assert result.output == ""


def test_mark_json_reports_what_was_marked(pane):
    result = runner.invoke(app, ["--json", "mark", "done"], env=hook_env(pane))

    data = json.loads(result.output)
    assert data == {"ok": True, "marked": True, "pane_id": pane.pane_id, "state": "done"}


def test_mark_clear_removes_all_hook_options(pane):
    runner.invoke(app, ["mark", "working", "--agent", "x"], env=hook_env(pane))

    result = runner.invoke(app, ["mark", "clear"], env=hook_env(pane))

    assert result.exit_code == 0
    for name in ("@twmux_state", "@twmux_state_ts", "@twmux_cmd", "@twmux_agent"):
        assert pane_option(pane, name) is None


def test_mark_outside_tmux_is_a_silent_no_op():
    """An agent started outside tmux still runs its hooks; they must not fail."""
    result = runner.invoke(app, ["mark", "working"], env={"TMUX": "", "TMUX_PANE": ""})

    assert result.exit_code == 0
    assert result.output == ""


def test_mark_is_a_no_op_when_opted_out(pane):
    """Nested agents launched from an agent pane set TWMUX_NO_MARK so only the
    outer agent drives the pane's state."""
    result = runner.invoke(app, ["mark", "working"], env=hook_env(pane, TWMUX_NO_MARK="1"))

    assert result.exit_code == 0
    assert pane_option(pane, "@twmux_state") is None


def test_mark_is_a_no_op_for_claude_background_jobs(pane):
    """Claude background workers inherit the pane of their supervisor; their
    hooks would overwrite the state of the session the user is looking at."""
    env = hook_env(pane, CLAUDE_JOB_DIR="/tmp/job-1")

    runner.invoke(app, ["mark", "working"], env=env)

    assert pane_option(pane, "@twmux_state") is None


def test_mark_rejects_unknown_state(pane):
    result = runner.invoke(app, ["--json", "mark", "busy"], env=hook_env(pane))

    assert result.exit_code == 1
    assert "busy" in json.loads(result.output)["error"]

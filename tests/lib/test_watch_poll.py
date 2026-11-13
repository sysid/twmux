"""Integration tests for the watch poll against a real (hermetic) tmux server."""

import time

from twmux.lib.classify import AgentConfig
from twmux.lib.watch import Config, Track, poll_server

# The test shell's prompt is "$ " (see conftest), so an idle "agent" is a
# bash pane whose title marks it as one.
FAKE_AGENT = AgentConfig(name="fake_agent", title_match="^fake-agent", re_idle=r"^\$")


def test_poll_server_reports_only_panes_matching_an_agent(session, pane):
    pane.cmd("select-pane", "-T", "fake-agent: refactor")
    other = session.active_window.split()
    other.cmd("select-pane", "-T", "plain shell")
    time.sleep(0.3)

    rows = poll_server(session.server, Config(poll_interval=2.0, agents=[FAKE_AGENT]), {}, 100.0)

    assert [r.pane_id for r in rows] == [pane.pane_id]
    (row,) = rows
    assert row.agent == "fake_agent"
    assert row.state == "idle"
    assert row.title == "fake-agent: refactor"
    assert row.target == f"{session.session_name}:{pane.window_index}.{pane.pane_index}"


def test_poll_server_keys_state_by_socket_and_pane(session, pane):
    """Pane ids restart at %0 on every tmux server, so the same id on another
    socket is a different agent: its state must not be read or overwritten."""
    pane.cmd("select-pane", "-T", "fake-agent: x")
    other_socket = ("some-other-socket", pane.pane_id)
    other_track = Track("working", 1.0, "h", 1.0, "", False, 1.0)
    state_map = {other_socket: other_track}

    (row,) = poll_server(
        session.server, Config(poll_interval=2.0, agents=[FAKE_AGENT]), state_map, 100.0
    )

    assert row.state == "idle"
    assert row.state_entered_at == 100.0
    assert state_map[other_socket] is other_track
    assert state_map[(session.server.socket_name, pane.pane_id)].state == "idle"


def mark(pane, state, agent="", command=None):
    """What `twmux mark` writes: state, time, the pane's command, agent name."""
    pane.cmd("set-option", "-p", "-t", pane.pane_id, "@twmux_state", state)
    pane.cmd("set-option", "-p", "-t", pane.pane_id, "@twmux_state_ts", "1700000000")
    if command is None:
        pane.cmd("set-option", "-pF", "-t", pane.pane_id, "@twmux_cmd", "#{pane_current_command}")
    else:
        pane.cmd("set-option", "-p", "-t", pane.pane_id, "@twmux_cmd", command)
    if agent:
        pane.cmd("set-option", "-p", "-t", pane.pane_id, "@twmux_agent", agent)


def poll(session):
    return poll_server(session.server, Config(poll_interval=2.0, agents=[FAKE_AGENT]), {}, 100.0)


def test_hook_state_wins_over_the_screen(session, pane):
    """The screen shows an idle prompt, but the agent's hook says it is
    working — the hook knows, the regex guesses."""
    pane.cmd("select-pane", "-T", "fake-agent: x")
    mark(pane, "working")

    (row,) = poll(session)

    assert row.state == "working"


def test_wait_on_screen_wins_over_hook_working(session, pane):
    """Some agents show permission prompts without emitting a hook; a visible
    wait prompt is never hidden behind a stale 'working'."""
    waiting = AgentConfig(name="fake_agent", title_match="^fake-agent", re_wait=r"^\$")
    pane.cmd("select-pane", "-T", "fake-agent: x")
    mark(pane, "working")

    (row,) = poll_server(session.server, Config(2.0, [waiting]), {}, 100.0)

    assert row.state == "wait"


def test_marked_pane_is_an_agent_even_if_no_regex_claims_it(session, pane):
    """Identity from the hook survives title/command drift of the agent TUI."""
    pane.cmd("select-pane", "-T", "something unrecognisable")
    mark(pane, "wait", agent="claude_code")

    (row,) = poll(session)

    assert row.agent == "claude_code"
    assert row.state == "wait"


def test_hook_state_of_an_exited_agent_is_ignored(session, pane):
    """The pane options outlive the agent process: once the pane runs another
    command than the one that marked it, the mark is stale."""
    pane.cmd("select-pane", "-T", "fake-agent: x")
    mark(pane, "working", command="2.1.139")

    (row,) = poll(session)

    assert row.state == "idle"


def test_hook_done_reads_as_idle(session, pane):
    pane.cmd("select-pane", "-T", "something unrecognisable")
    mark(pane, "done", agent="claude_code")

    (row,) = poll(session)

    assert row.state == "idle"


def window_option(window, name):
    out = window.cmd("show-options", "-w", "-v", "-t", window.window_id, name).stdout
    return out[0] if out else None


def test_sync_window_status_sets_and_clears_the_window_icon(session, pane):
    from twmux.lib.watch import sync_window_status

    window = session.active_window
    key = (session.server.socket_name, window.window_id)
    written: dict = {}

    sync_window_status(session.server, {key: "💬"}, written)
    assert window_option(window, "@twmux_status") == "💬"

    sync_window_status(session.server, {}, written)
    assert window_option(window, "@twmux_status") is None
    assert written == {}


def test_poll_rows_carry_their_window_id(session, pane):
    pane.cmd("select-pane", "-T", "fake-agent: x")

    (row,) = poll(session)

    assert row.window_id == session.active_window.window_id

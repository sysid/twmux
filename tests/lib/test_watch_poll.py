"""Integration tests for the watch poll against a real (hermetic) tmux server."""

import time

from twmux.lib.classify import AgentConfig
from twmux.lib.watch import Config, poll_server

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
    state_map = {other_socket: ("working", 1.0)}

    (row,) = poll_server(
        session.server, Config(poll_interval=2.0, agents=[FAKE_AGENT]), state_map, 100.0
    )

    assert row.state == "idle"
    assert row.state_entered_at == 100.0
    assert state_map[other_socket] == ("working", 1.0)
    assert state_map[(session.server.socket_name, pane.pane_id)] == ("idle", 100.0)

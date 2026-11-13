"""Tests for the pure-logic parts of watch: state tracking, sort, formatting."""

from twmux.lib.watch import (
    AgentRow,
    ensure_config,
    format_wait,
    load_config,
    merge_state,
    render_tsv,
    sort_rows,
    state_priority,
)


def make_row(
    state: str,
    entered_at: float,
    pane_id: str = "%1",
    title: str = "",
    last_line: str = "",
) -> AgentRow:
    return AgentRow(
        pane_id=pane_id,
        socket="sock",
        target=f"sess:1.{pane_id.lstrip('%')}",
        project="proj",
        agent="claude_code",
        state=state,
        state_entered_at=entered_at,
        title=title,
        last_line=last_line,
    )


# -- state_priority ------------------------------------------------------------


def test_state_priority_order():
    """wait > idle > working > unknown."""
    assert state_priority("wait") > state_priority("idle")
    assert state_priority("idle") > state_priority("working")
    assert state_priority("working") > state_priority("unknown")


# -- merge_state ---------------------------------------------------------------


def test_merge_state_new_pane_sets_entered_at_to_now():
    row = merge_state(prev=None, observed_state="working", now=1000.0)
    assert row == ("working", 1000.0)


def test_merge_state_unchanged_preserves_entered_at():
    row = merge_state(prev=("idle", 500.0), observed_state="idle", now=1000.0)
    assert row == ("idle", 500.0)


def test_merge_state_changed_updates_entered_at():
    row = merge_state(prev=("working", 500.0), observed_state="wait", now=1000.0)
    assert row == ("wait", 1000.0)


# -- format_wait ---------------------------------------------------------------


def test_format_wait_zero_is_dash():
    assert format_wait(0) == "-"


def test_format_wait_seconds_only():
    assert format_wait(45) == "45s"


def test_format_wait_minutes_and_seconds():
    assert format_wait(312) == "5m12s"


def test_format_wait_hours_minutes():
    assert format_wait(3725) == "1h2m"


# -- sort_rows -----------------------------------------------------------------


def test_sort_wait_before_idle_before_working():
    rows = [
        make_row("working", entered_at=100.0, pane_id="%1"),
        make_row("idle", entered_at=200.0, pane_id="%2"),
        make_row("wait", entered_at=300.0, pane_id="%3"),
    ]
    out = sort_rows(rows, now=400.0)
    assert [r.pane_id for r in out] == ["%3", "%2", "%1"]


def test_sort_within_same_state_shorter_wait_first():
    """Within a state bucket, the more recently observed (shorter wait)
    pane comes first — long-idle agents sink to the bottom."""
    rows = [
        make_row("wait", entered_at=200.0, pane_id="%1"),  # 200s waiting
        make_row("wait", entered_at=100.0, pane_id="%2"),  # 300s waiting
    ]
    out = sort_rows(rows, now=400.0)
    assert [r.pane_id for r in out] == ["%1", "%2"]


def test_sort_working_panes_dont_use_wait_time_for_secondary_sort():
    """Working state is not 'waiting' — secondary sort still works but the
    primary state bucket keeps them at the bottom."""
    rows = [
        make_row("working", entered_at=50.0, pane_id="%w1"),
        make_row("idle", entered_at=300.0, pane_id="%i1"),  # 100s idle
        make_row("working", entered_at=10.0, pane_id="%w2"),
    ]
    out = sort_rows(rows, now=400.0)
    # idle bucket first, then working bucket
    assert out[0].pane_id == "%i1"
    assert {out[1].pane_id, out[2].pane_id} == {"%w1", "%w2"}


def test_sort_empty_list_returns_empty():
    assert sort_rows([], now=0.0) == []


# -- render_tsv ----------------------------------------------------------------


def test_render_tsv_includes_title_column_between_project_and_last_line():
    """The pane title is surfaced as its own column so the switcher can show
    what each agent is working on. Column order must stay
    wait, state, agent, target, project, title, last_line — the switcher and
    its preview/jump logic index columns positionally (target is field 4)."""
    row = make_row(
        "wait",
        entered_at=100.0,
        title="Add titles to switcher",
        last_line="some output",
    )
    line = render_tsv([row], now=160.0).rstrip("\n")
    fields = line.split("\t")
    assert fields == [
        "1m00s",  # wait
        "wait",  # state
        "claude_code",  # agent
        "sess:1.1",  # target
        "proj",  # project
        "Add titles to switcher",  # title
        "some output",  # last_line
    ]


# -- logging setup -------------------------------------------------------------


# -- stop_daemon ---------------------------------------------------------------


def test_stop_daemon_missing_pid_file_returns_not_running(tmp_path, monkeypatch):
    """If no PID file exists, stop is a no-op success."""
    from twmux.lib import watch

    monkeypatch.setattr(watch, "PID_PATH", tmp_path / "no-such-pid")
    result = watch.stop_daemon(timeout=0.1)
    assert result.status == "not_running"


def test_stop_daemon_stale_pid_file_returns_not_running(tmp_path, monkeypatch):
    """PID file present but the process is gone (stale) — treat as not running."""
    from twmux.lib import watch

    pid_path = tmp_path / "watch.pid"
    pid_path.write_text("999999")  # almost certainly dead
    monkeypatch.setattr(watch, "PID_PATH", pid_path)
    monkeypatch.setattr(watch, "_pid_alive", lambda _pid: False)
    result = watch.stop_daemon(timeout=0.1)
    assert result.status == "not_running"


def test_stop_daemon_kills_and_waits(tmp_path, monkeypatch):
    """Happy path: sends SIGTERM, polls until process dies."""
    import os
    import signal as signal_mod

    from twmux.lib import watch

    pid_path = tmp_path / "watch.pid"
    pid_path.write_text("12345")
    monkeypatch.setattr(watch, "PID_PATH", pid_path)

    alive = [True, True, False]  # dies on the third probe

    monkeypatch.setattr(watch, "_pid_alive", lambda _pid: alive.pop(0))
    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    result = watch.stop_daemon(timeout=2.0, poll_interval=0.01)
    assert result.status == "stopped"
    assert result.pid == 12345
    assert killed == [(12345, signal_mod.SIGTERM)]


def test_ensure_singleton_blocks_concurrent_second_acquire(tmp_path, monkeypatch):
    """Race-resistance: when two daemons start at nearly the same time, only
    one may hold the singleton. The previous check-then-write implementation
    had a TOCTOU window and allowed multiple daemons to coexist, racing on
    _atomic_write of the TSV. A kernel-level flock closes that window.

    Verified in-process by simulating two independent acquisitions against
    the same lock file: the first succeeds, a second from a different FD
    must fail, and once the first releases, acquisition becomes possible
    again.
    """
    import os

    from twmux.lib import watch

    monkeypatch.setattr(watch, "PID_PATH", tmp_path / "watch.pid")
    monkeypatch.setattr(watch, "_lock_fd", None)

    assert watch.ensure_singleton() is True
    first_fd = watch._lock_fd
    assert first_fd is not None

    # Simulate a second daemon process invoking ensure_singleton — fresh
    # FD opened inside the call, but the kernel-held lock on first_fd
    # must block acquisition.
    monkeypatch.setattr(watch, "_lock_fd", None)
    assert watch.ensure_singleton() is False

    # After releasing the first holder, acquisition is possible again.
    os.close(first_fd)
    monkeypatch.setattr(watch, "_lock_fd", None)
    assert watch.ensure_singleton() is True
    watch._release_singleton()


def test_setup_logging_uses_rotating_file_handler(tmp_path):
    """Daemon logs must be size-bounded to avoid filling the disk on a
    crash loop. RotatingFileHandler caps each file and keeps N backups."""
    import logging
    from logging.handlers import RotatingFileHandler

    from twmux.lib.watch import setup_logging

    log_file = tmp_path / "watch.log"
    logger = setup_logging(log_file, max_bytes=1024, backup_count=2)

    handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(handlers) == 1, "exactly one rotating handler expected"
    assert handlers[0].maxBytes == 1024
    assert handlers[0].backupCount == 2
    assert handlers[0].baseFilename == str(log_file)

    logger.warning("hello")
    assert log_file.exists()

    # Cleanup so other tests don't see stale handlers
    for h in handlers:
        logger.removeHandler(h)
        h.close()
    logging.shutdown()


# -- ensure_config -------------------------------------------------------------


def test_ensure_config_seeds_missing_file_from_packaged_example(tmp_path):
    """First run: no config yet → the packaged example is copied in (parent
    dirs included), so a fresh install gets working agent matchers."""
    path = tmp_path / "twmux" / "agents.toml"

    created = ensure_config(path)

    assert created is True
    agent_names = [agent.name for agent in load_config(path).agents]
    assert "claude_code" in agent_names
    assert "copilot_cli" in agent_names


def test_ensure_config_leaves_existing_file_untouched(tmp_path):
    """A user-tuned config must never be overwritten by the example."""
    path = tmp_path / "agents.toml"
    path.write_text("poll_interval = 5.0\n")

    created = ensure_config(path)

    assert created is False
    assert path.read_text() == "poll_interval = 5.0\n"


# -- snapshot parsing ----------------------------------------------------------


def test_parse_snapshot_reads_one_pane_per_line():
    """One list-panes call yields every field the poll needs, unit-separated."""
    from twmux.lib.watch import parse_snapshot

    line = "\x1f".join(
        [
            "%7",
            "node",
            "⠐ codex | fix",
            "/src/proj",
            "work",
            "@3",
            "2",
            "1",
            "1",
            "1",
            "",
            "",
            "",
            "",
        ]
    )

    (pane,) = parse_snapshot([line])

    assert pane.pane_id == "%7"
    assert pane.command == "node"
    assert pane.title == "⠐ codex | fix"
    assert pane.path == "/src/proj"
    assert pane.target == "work:2.1"
    assert pane.window_id == "@3"
    assert pane.visible is True


def test_parse_snapshot_pane_in_inactive_window_is_not_visible():
    from twmux.lib.watch import parse_snapshot

    line = "\x1f".join(["%7", "bash", "", "/", "work", "@3", "2", "1", "0", "1", "", "", "", ""])

    (pane,) = parse_snapshot([line])

    assert pane.visible is False


def test_parse_snapshot_pane_in_detached_session_is_not_visible():
    from twmux.lib.watch import parse_snapshot

    line = "\x1f".join(["%7", "bash", "", "/", "work", "@3", "2", "1", "1", "0", "", "", "", ""])

    (pane,) = parse_snapshot([line])

    assert pane.visible is False


def test_parse_snapshot_skips_malformed_lines():
    """A line with the wrong field count (e.g. a title containing the
    separator) is dropped rather than shifting every later field."""
    from twmux.lib.watch import parse_snapshot

    assert parse_snapshot(["garbage", ""]) == []


def test_parse_snapshot_reads_the_hook_mark_made_under_the_current_command():
    from twmux.lib.watch import parse_snapshot

    line = "\x1f".join(
        [
            "%7",
            "2.1.139",
            "x",
            "/",
            "s",
            "@3",
            "2",
            "1",
            "1",
            "1",
            "wait",
            "1700000000",
            "2.1.139",
            "cc",
        ]
    )

    (pane,) = parse_snapshot([line])

    assert pane.hook_state == "wait"
    assert pane.hook_agent == "cc"


def test_parse_snapshot_drops_the_hook_mark_once_the_command_changed():
    """The agent exited and the pane is back at its shell: the mark is stale."""
    from twmux.lib.watch import parse_snapshot

    line = "\x1f".join(
        [
            "%7",
            "bash",
            "x",
            "/",
            "s",
            "@3",
            "2",
            "1",
            "1",
            "1",
            "wait",
            "1700000000",
            "2.1.139",
            "cc",
        ]
    )

    (pane,) = parse_snapshot([line])

    assert pane.hook_state is None
    assert pane.hook_agent == ""


def test_parse_snapshot_ignores_an_unknown_hook_state():
    from twmux.lib.watch import parse_snapshot

    line = "\x1f".join(
        ["%7", "bash", "x", "/", "s", "@3", "2", "1", "1", "1", "busy", "1", "bash", ""]
    )

    (pane,) = parse_snapshot([line])

    assert pane.hook_state is None


# -- next_track: hook staleness ------------------------------------------------
#
# Hooks miss events: Ctrl-C/Escape fire no Stop hook, a rejected permission
# prompt fires nothing. The screen is the cross-check.


def hooked_pane(hook_state, hook_ts="1700000000"):
    from twmux.lib.watch import PaneInfo

    return PaneInfo(
        pane_id="%1",
        command="2.1.139",
        title="",
        path="/p",
        target="s:1.1",
        window_id="@1",
        visible=False,
        hook_state=hook_state,
        hook_ts=hook_ts,
    )


def test_working_hook_holds_while_the_screen_keeps_changing():
    from twmux.lib.watch import next_track

    t = next_track(None, hooked_pane("working"), "idle", "h1", now=0.0, stale_secs=10)
    t = next_track(t, hooked_pane("working"), "idle", "h2", now=8.0, stale_secs=10)
    t = next_track(t, hooked_pane("working"), "idle", "h3", now=16.0, stale_secs=10)

    assert t.state == "working"


def test_working_hook_goes_stale_when_the_screen_freezes():
    """Ctrl-C: the agent stops without a Stop hook. A working agent redraws its
    spinner every second, so a frozen screen means it is not working."""
    from twmux.lib.watch import next_track

    t = next_track(None, hooked_pane("working"), "idle", "same", now=0.0, stale_secs=10)
    t = next_track(t, hooked_pane("working"), "idle", "same", now=9.0, stale_secs=10)
    assert t.state == "working"

    t = next_track(t, hooked_pane("working"), "idle", "same", now=10.0, stale_secs=10)
    assert t.state == "idle"


def test_wait_hook_holds_on_a_static_screen():
    """A permission prompt does not redraw; a frozen screen is expected."""
    from twmux.lib.watch import next_track

    t = next_track(None, hooked_pane("wait"), "idle", "prompt", now=0.0, stale_secs=10)
    t = next_track(t, hooked_pane("wait"), "idle", "prompt", now=600.0, stale_secs=10)

    assert t.state == "wait"


def test_wait_hook_goes_stale_once_the_screen_changed_and_settled():
    """The user rejected the prompt with Escape — no hook fires, but the
    screen changed after the mark and then stayed put."""
    from twmux.lib.watch import next_track

    t = next_track(None, hooked_pane("wait"), "idle", "prompt", now=0.0, stale_secs=10)
    t = next_track(t, hooked_pane("wait"), "idle", "rejected", now=2.0, stale_secs=10)
    assert t.state == "wait"

    t = next_track(t, hooked_pane("wait"), "idle", "rejected", now=12.0, stale_secs=10)
    assert t.state == "idle"


def test_a_new_mark_is_trusted_again():
    from twmux.lib.watch import next_track

    t = next_track(None, hooked_pane("wait", "1"), "idle", "a", now=0.0, stale_secs=10)
    t = next_track(t, hooked_pane("wait", "1"), "idle", "b", now=2.0, stale_secs=10)
    t = next_track(t, hooked_pane("wait", "1"), "idle", "b", now=12.0, stale_secs=10)
    assert t.state == "idle"

    t = next_track(t, hooked_pane("wait", "2"), "idle", "b", now=14.0, stale_secs=10)
    assert t.state == "wait"


def test_next_track_keeps_entered_at_while_the_state_holds():
    from twmux.lib.watch import next_track

    t = next_track(None, hooked_pane("wait"), "idle", "a", now=5.0, stale_secs=10)
    t = next_track(t, hooked_pane("wait"), "idle", "a", now=50.0, stale_secs=10)

    assert t.entered_at == 5.0


def test_load_config_reads_hook_stale_secs(tmp_path):
    path = tmp_path / "agents.toml"
    path.write_text("hook_stale_secs = 30\n")

    assert load_config(path).hook_stale_secs == 30.0


def test_load_config_defaults_hook_stale_secs_to_ten(tmp_path):
    path = tmp_path / "agents.toml"
    path.write_text("poll_interval = 2.0\n")

    assert load_config(path).hook_stale_secs == 10.0


# -- done: finished but not yet seen -------------------------------------------


def pane_at(visible, hook_state=None):
    from twmux.lib.watch import PaneInfo

    return PaneInfo(
        pane_id="%1",
        command="2.1.139",
        title="",
        path="/p",
        target="s:1.1",
        window_id="@1",
        visible=visible,
        hook_state=hook_state,
    )


def test_agent_finishing_out_of_sight_is_done():
    from twmux.lib.watch import display_state, next_track

    t = next_track(None, pane_at(visible=False), "working", "a", now=0.0, stale_secs=10)
    t = next_track(t, pane_at(visible=False), "idle", "b", now=2.0, stale_secs=10)

    assert display_state(t) == "done"


def test_agent_finishing_in_front_of_the_user_is_just_idle():
    from twmux.lib.watch import display_state, next_track

    t = next_track(None, pane_at(visible=True), "working", "a", now=0.0, stale_secs=10)
    t = next_track(t, pane_at(visible=True), "idle", "b", now=2.0, stale_secs=10)

    assert display_state(t) == "idle"


def test_done_becomes_idle_once_its_window_was_visible():
    from twmux.lib.watch import display_state, next_track

    t = next_track(None, pane_at(visible=False), "working", "a", now=0.0, stale_secs=10)
    t = next_track(t, pane_at(visible=False), "idle", "b", now=2.0, stale_secs=10)
    t = next_track(t, pane_at(visible=True), "idle", "b", now=4.0, stale_secs=10)
    t = next_track(t, pane_at(visible=False), "idle", "b", now=6.0, stale_secs=10)

    assert display_state(t) == "idle"


def test_idle_agents_found_at_daemon_start_are_not_done():
    """No history means no evidence the user missed anything — a daemon
    restart must not flood the list with done rows."""
    from twmux.lib.watch import display_state, next_track

    t = next_track(None, pane_at(visible=False), "idle", "a", now=0.0, stale_secs=10)

    assert display_state(t) == "idle"


def test_hook_done_out_of_sight_is_done():
    from twmux.lib.watch import display_state, next_track

    t = next_track(None, pane_at(False, "working"), "unknown", "a", now=0.0, stale_secs=10)
    t = next_track(t, pane_at(False, "done"), "unknown", "b", now=2.0, stale_secs=10)

    assert display_state(t) == "done"


def test_state_priority_done_between_wait_and_idle():
    assert state_priority("wait") > state_priority("done") > state_priority("idle")


def test_sort_puts_done_after_wait_and_before_idle():
    rows = [
        make_row("idle", entered_at=300.0, pane_id="%1"),
        make_row("done", entered_at=100.0, pane_id="%2"),
        make_row("wait", entered_at=200.0, pane_id="%3"),
    ]

    out = sort_rows(rows, now=400.0)

    assert [r.pane_id for r in out] == ["%3", "%2", "%1"]


# -- agents.json ---------------------------------------------------------------


def test_render_json_carries_socket_pane_and_epoch_for_tools():
    import json

    from twmux.lib.watch import render_json

    row = make_row("done", entered_at=100.0, pane_id="%4", title="t")

    (entry,) = json.loads(render_json([row]))

    assert entry == {
        "socket": "sock",
        "pane_id": "%4",
        "target": "sess:1.4",
        "project": "proj",
        "agent": "claude_code",
        "state": "done",
        "state_entered_at": 100.0,
        "title": "t",
    }


# -- pick_next: jump to the newest waiting/done agent, cycling -------------------


def row_on(socket, pane_id, state, entered_at):
    return AgentRow(
        pane_id=pane_id,
        socket=socket,
        target=f"s:1.{pane_id.lstrip('%')}",
        project="p",
        agent="claude_code",
        state=state,
        state_entered_at=entered_at,
        title="",
        last_line="",
    )


def test_pick_next_takes_the_most_recent_wait_or_done_agent():
    from twmux.lib.watch import pick_next

    rows = [
        row_on("main", "%1", "done", 100.0),
        row_on("main", "%2", "wait", 300.0),
        row_on("main", "%3", "working", 400.0),
        row_on("main", "%4", "idle", 500.0),
    ]

    picked, _ = pick_next(rows, socket="main", current_pane="", cursor=None)

    assert picked.pane_id == "%2"


def test_pick_next_cycles_to_older_agents_on_repeated_calls():
    from twmux.lib.watch import pick_next

    rows = [row_on("main", "%1", "done", 100.0), row_on("main", "%2", "wait", 300.0)]

    first, cursor = pick_next(rows, "main", "", None)
    second, cursor = pick_next(rows, "main", "", cursor)
    third, cursor = pick_next(rows, "main", "", cursor)

    assert [first.pane_id, second.pane_id, third.pane_id] == ["%2", "%1", "%2"]


def test_pick_next_restarts_at_the_newest_when_another_agent_finishes():
    from twmux.lib.watch import pick_next

    rows = [row_on("main", "%1", "done", 100.0), row_on("main", "%2", "wait", 300.0)]
    _, cursor = pick_next(rows, "main", "", None)
    _, cursor = pick_next(rows, "main", "", cursor)

    rows.append(row_on("main", "%3", "done", 600.0))
    picked, _ = pick_next(rows, "main", "", cursor)

    assert picked.pane_id == "%3"


def test_pick_next_skips_the_pane_the_user_is_in():
    from twmux.lib.watch import pick_next

    rows = [row_on("main", "%1", "done", 100.0), row_on("main", "%2", "wait", 300.0)]

    picked, _ = pick_next(rows, "main", current_pane="%2", cursor=None)

    assert picked.pane_id == "%1"


def test_pick_next_only_considers_agents_on_the_callers_server():
    """switch-client can only reach panes on the client's own tmux server."""
    from twmux.lib.watch import pick_next

    rows = [row_on("other", "%1", "wait", 900.0), row_on("main", "%1", "done", 100.0)]

    picked, _ = pick_next(rows, "main", "", None)

    assert (picked.socket, picked.pane_id) == ("main", "%1")


def test_pick_next_without_candidates_picks_nothing():
    from twmux.lib.watch import pick_next

    picked, _ = pick_next([row_on("main", "%1", "working", 1.0)], "main", "", None)

    assert picked is None


# -- status-bar icons ------------------------------------------------------------

ICONS = {"wait": "W", "done": "D", "working": "R"}


def row_in_window(window_id, state, pane_id="%1", socket="main"):
    return AgentRow(
        pane_id=pane_id,
        socket=socket,
        target="",
        project="p",
        agent="a",
        state=state,
        state_entered_at=0.0,
        title="",
        last_line="",
        window_id=window_id,
    )


def test_window_icons_show_the_most_urgent_agent_of_each_window():
    from twmux.lib.watch import window_icons

    rows = [
        row_in_window("@1", "working", "%1"),
        row_in_window("@1", "wait", "%2"),
        row_in_window("@2", "done", "%3"),
    ]

    assert window_icons(rows, ICONS) == {("main", "@1"): "W", ("main", "@2"): "D"}


def test_window_icons_leave_idle_windows_without_icon():
    from twmux.lib.watch import window_icons

    rows = [row_in_window("@1", "idle"), row_in_window("@2", "unknown", "%2")]

    assert window_icons(rows, ICONS) == {}


def test_window_icons_keep_windows_of_different_servers_apart():
    from twmux.lib.watch import window_icons

    rows = [row_in_window("@1", "wait", socket="a"), row_in_window("@1", "done", socket="b")]

    assert window_icons(rows, ICONS) == {("a", "@1"): "W", ("b", "@1"): "D"}


def test_load_config_status_icons_override_defaults(tmp_path):
    path = tmp_path / "agents.toml"
    path.write_text('status_icons = { wait = "?" }\n')

    icons = load_config(path).status_icons

    assert icons["wait"] == "?"
    assert icons["done"]  # default kept


def test_load_config_status_icons_table_is_not_an_agent(tmp_path):
    path = tmp_path / "agents.toml"
    path.write_text('[status_icons]\nwait = "?"\n')

    assert load_config(path).agents == []

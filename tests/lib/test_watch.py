"""Tests for the pure-logic parts of watch: state tracking, sort, formatting."""

from twmux.lib.watch import (
    AgentRow,
    format_wait,
    merge_state,
    sort_rows,
    state_priority,
)


def make_row(state: str, entered_at: float, pane_id: str = "%1") -> AgentRow:
    return AgentRow(
        pane_id=pane_id,
        target=f"sess:1.{pane_id.lstrip('%')}",
        project="proj",
        agent="claude_code",
        state=state,
        state_entered_at=entered_at,
        last_line="",
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

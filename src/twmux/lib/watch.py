"""Watch daemon: poll panes across sockets, classify state, write sorted TSV.

Split into two layers:
- Pure logic (sort, merge, format) — unit-tested in tests/lib/test_watch.py.
- libtmux glue (poll loop, pane enumeration, file I/O) — tested manually
  per the v1 plan.
"""

from __future__ import annotations

import fcntl
import logging
import os
import signal
import socket as socket_module
import sys
import time
import tomllib
from dataclasses import dataclass
from importlib import resources
from logging.handlers import RotatingFileHandler
from pathlib import Path

from twmux.lib.classify import AgentConfig, classify
from twmux.lib.safety import enumerate_all_sockets, get_socket_dir

State = str  # "wait" | "working" | "idle" | "unknown"

# Lower-numbered states are less actionable; sort DESC so high-priority first.
_STATE_PRIORITY = {"wait": 3, "idle": 2, "working": 1, "unknown": 0}


@dataclass(frozen=True)
class AgentRow:
    """One observed agent pane, with its current state and tracking metadata."""

    pane_id: str  # libtmux %N id
    target: str  # session:window.pane address for tmux commands
    project: str  # basename of pane cwd (display only)
    agent: str  # agent config name (e.g. "claude_code")
    state: State
    state_entered_at: float
    title: str  # pane_title set by the running program (display only)
    last_line: str


# ----------------------------------------------------------------------------
# Pure logic
# ----------------------------------------------------------------------------


def state_priority(state: State) -> int:
    """Higher = more urgent. Used for primary sort key."""
    return _STATE_PRIORITY.get(state, 0)


def merge_state(
    prev: tuple[State, float] | None,
    observed_state: State,
    now: float,
) -> tuple[State, float]:
    """Return (state, state_entered_at) for the new poll.

    If state matches the previous observation, preserve the original
    entered_at so wait_secs keeps climbing. Otherwise reset to now.
    """
    if prev is None or prev[0] != observed_state:
        return (observed_state, now)
    return prev


def format_wait(secs: float) -> str:
    """Human-readable elapsed time, '-' for zero."""
    s = int(secs)
    if s <= 0:
        return "-"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60}m"


def sort_rows(rows: list[AgentRow], now: float) -> list[AgentRow]:
    """Sort by state priority DESC, then by waiting time ASC.

    State priority puts the most actionable bucket (wait) at the top. Within
    each bucket, shorter wait_secs comes first so the most recently observed
    panes are at the top of the popup — the long-idle tail sinks to the
    bottom where it's easy to ignore.
    """
    return sorted(
        rows,
        key=lambda r: (-state_priority(r.state), now - r.state_entered_at),
    )


def render_tsv(rows: list[AgentRow], now: float) -> str:
    """Serialize rows to TSV, one line per row. No header (column names are
    static; the switcher script supplies them via fzf --header)."""
    lines = []
    for r in rows:
        wait = format_wait(now - r.state_entered_at) if r.state != "working" else "-"
        lines.append("\t".join([wait, r.state, r.agent, r.target, r.project, r.title, r.last_line]))
    return "\n".join(lines) + ("\n" if lines else "")


# ----------------------------------------------------------------------------
# Config loading
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    poll_interval: float
    agents: list[AgentConfig]


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "twmux" / "agents.toml"
EXAMPLE_CONFIG = resources.files("twmux") / "agents.toml"


def ensure_config(path: Path = DEFAULT_CONFIG_PATH) -> bool:
    """Seed the config from the packaged example if missing.

    Returns True if the file was created. An existing file is never touched —
    it holds the user's tuned regexes.
    """
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load TOML config; return defaults if missing."""
    if not path.exists():
        return Config(poll_interval=2.0, agents=[])

    with path.open("rb") as f:
        data = tomllib.load(f)

    poll_interval = float(data.pop("poll_interval", 2.0))
    agents = []
    for name, section in data.items():
        if not isinstance(section, dict):
            continue
        agents.append(
            AgentConfig(
                name=name,
                cmd_match=section.get("cmd_match"),
                title_match=section.get("title_match"),
                content_match=section.get("content_match"),
                re_working=section.get("re_working"),
                re_wait=section.get("re_wait"),
                re_idle=section.get("re_idle"),
            )
        )
    return Config(poll_interval=poll_interval, agents=agents)


# ----------------------------------------------------------------------------
# libtmux glue — not unit-tested; verified manually per the v1 plan
# ----------------------------------------------------------------------------


CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "twmux"
TSV_PATH = CACHE_DIR / "agents.tsv"
PID_PATH = CACHE_DIR / "watch.pid"
LOG_PATH = CACHE_DIR / "watch.log"

# Log rotation defaults: 1 MB per file × 3 backups = max 4 MB on disk
# (current + 3 backups). Bounds the worst-case crash-loop log growth without
# losing useful diagnostic history.
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3


def setup_logging(
    path: Path = LOG_PATH,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_COUNT,
) -> logging.Logger:
    """Attach a size-bounded rotating file handler to the daemon logger.

    Without this, the daemon's stderr is either discarded (tmux run-shell -b)
    or redirected to an unbounded file by launchd. A crash loop can grow
    those files indefinitely. RotatingFileHandler caps total disk use at
    (max_bytes * (backup_count + 1)).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("twmux.watch")
    logger.setLevel(logging.INFO)

    # Remove any prior handler the same logger already has (re-setup in tests).
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    handler = RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)
    logger.propagate = False  # Don't double-log via the root logger.
    return logger


def _socket_alive(socket_name: str, timeout: float = 0.05) -> bool:
    """Fast liveness probe for a tmux socket file.

    Connecting via Unix-domain socket succeeds (~ms) for live tmux servers
    and fails fast (ECONNREFUSED or ENOENT) for stale socket files left
    behind by dead servers. Without this, libtmux's per-socket probe takes
    ~20ms each — at 500 stale sockets that's 10s per poll cycle.
    """
    path = get_socket_dir() / socket_name
    s = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _pane_title(pane) -> str:
    """Read pane_title via tmux's display-message — libtmux doesn't expose it
    as an attribute on the Pane object."""
    try:
        result = pane.cmd("display-message", "-p", "-t", pane.pane_id, "#{pane_title}")
        if result.stdout:
            return result.stdout[0]
    except Exception:
        pass
    return ""


def _match_agent(
    cmd: str, title: str, agents: list[AgentConfig], content: str = ""
) -> AgentConfig | None:
    """Find first agent config that matches this pane.

    An agent matches if either its cmd_match regex hits the pane's current
    command OR its title_match regex hits the pane title. If the matching
    config also has content_match, the pane's captured text must satisfy
    that regex too — otherwise we skip to the next candidate.
    """
    import re

    for cfg in agents:
        hit = False
        if cfg.cmd_match and re.search(cfg.cmd_match, cmd):
            hit = True
        elif cfg.title_match and re.search(cfg.title_match, title):
            hit = True
        if hit:
            if cfg.content_match and not re.search(cfg.content_match, content, re.MULTILINE):
                continue
            return cfg
    return None


def _basename_cwd(cwd: str | None) -> str:
    if not cwd:
        return ""
    return os.path.basename(cwd.rstrip("/"))


def _last_nonblank_line(captured: list[str]) -> str:
    for line in reversed(captured):
        s = line.strip()
        if s:
            return s
    return ""


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(content)
    tmp.replace(path)


def _poll_once(
    config: Config,
    state_map: dict[str, tuple[State, float]],
    now: float,
) -> list[AgentRow]:
    """Enumerate every pane across every socket; build a fresh row list and
    update state_map in place (prune panes that vanished)."""
    from libtmux import Server

    rows: list[AgentRow] = []
    seen: set[str] = set()

    for sock in enumerate_all_sockets():
        if not _socket_alive(sock):
            continue
        try:
            server = Server(socket_name=sock)
            sessions = server.sessions
        except Exception:
            continue

        for session in sessions:
            for window in session.windows:
                for pane in window.panes:
                    cmd = pane.pane_current_command or ""
                    title = _pane_title(pane)

                    # Capture visible area before matching so content_match
                    # can gate agent identification (e.g. "node" + footer).
                    captured = pane.capture_pane() or []
                    text = "\n".join(captured)

                    cfg = _match_agent(cmd, title, config.agents, content=text)
                    if cfg is None:
                        continue

                    state = classify(text, cfg)

                    # libtmux types pane_id as Optional[str] because it's
                    # generic across all tmux format vars, but tmux always
                    # populates #{pane_id} for a real pane. Assert the
                    # invariant so a contract change would fail loudly here
                    # rather than silently corrupt state_map.
                    pane_id = pane.pane_id
                    assert pane_id is not None
                    prev = state_map.get(pane_id)
                    new_state, entered = merge_state(prev, state, now)
                    state_map[pane_id] = (new_state, entered)
                    seen.add(pane_id)

                    target = f"{session.session_name}:{window.window_index}.{pane.pane_index}"
                    rows.append(
                        AgentRow(
                            pane_id=pane_id,
                            target=target,
                            project=_basename_cwd(pane.pane_current_path),
                            agent=cfg.name,
                            state=new_state,
                            state_entered_at=entered,
                            title=title,
                            last_line=_last_nonblank_line(captured),
                        )
                    )

    # Prune state_map of panes that disappeared.
    for stale_pane in [pid for pid in state_map if pid not in seen]:
        state_map.pop(stale_pane, None)

    return rows


# ----------------------------------------------------------------------------
# PID-file singleton enforcement
# ----------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


# Module-level fd that holds the singleton lock for the lifetime of the
# daemon process. Kept open intentionally — flock is released when the FD
# closes (normal exit, crash, SIGKILL), so the OS cleans up for us.
_lock_fd: int | None = None


def ensure_singleton() -> bool:
    """Acquire an exclusive, non-blocking flock on PID_PATH.

    Returns True if we acquired the lock, False if another daemon already
    holds it. The previous implementation did a check-then-write on
    PID_PATH; two daemons starting concurrently both saw "no live owner"
    and both wrote their PID, racing forever on _atomic_write of the TSV.
    flock is OS-enforced and race-free.

    On success the file is truncated and our PID is written into it so
    `twmux watch stop` and external observers know which process to
    signal. The PID file is a courtesy; the singleton guarantee comes
    from the kernel lock on _lock_fd, not from the file's content.

    After acquiring the lock, if the PID file contained a still-alive PID
    (from an old daemon that predates the flock mechanism), SIGTERM it so
    we don't coexist with a legacy daemon that never held the lock.
    """
    global _lock_fd
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(PID_PATH, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False

    # We hold the lock. Check if there's a stale daemon (pre-flock era)
    # that is alive but doesn't hold the lock.
    try:
        existing = os.pread(fd, 32, 0).decode().strip()
        if existing:
            old_pid = int(existing)
            if old_pid != os.getpid() and _pid_alive(old_pid):
                os.kill(old_pid, signal.SIGTERM)
                # Brief wait for clean exit
                for _ in range(20):
                    time.sleep(0.1)
                    if not _pid_alive(old_pid):
                        break
    except (ValueError, OSError):
        pass

    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, str(os.getpid()).encode())
    os.fsync(fd)
    _lock_fd = fd
    return True


def _release_singleton() -> None:
    """Release the flock, close the lock FD, and remove the PID file.

    Idempotent. Called from the daemon's `finally` block so that crashes
    via SIGTERM/KeyboardInterrupt still clean up. SIGKILL skips this but
    the kernel closes the FD anyway, releasing the flock.
    """
    global _lock_fd
    if _lock_fd is not None:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(_lock_fd)
        except OSError:
            pass
        _lock_fd = None
    try:
        PID_PATH.unlink()
    except OSError:
        pass


# ----------------------------------------------------------------------------
# Daemon entrypoint
# ----------------------------------------------------------------------------


def run_daemon(
    config: Config,
    ensure_running: bool,
    log: bool = False,
) -> int:
    """Polling loop. Returns 0 on graceful exit, 1 if singleton failed."""
    logger = setup_logging()

    if not ensure_singleton():
        msg = "another daemon is already running"
        logger.info(msg)
        if log:
            print(f"twmux watch: {msg}", file=sys.stderr)
        # --ensure-running means "be idempotent" (exit 0); without it,
        # failing to acquire singleton is an error (exit 1).
        return 0 if ensure_running else 1

    # Translate SIGTERM into KeyboardInterrupt so the `finally` block runs.
    # Without this, `tmux kill-server` (sends SIGTERM) leaves a stale PID file.
    def _on_sigterm(*_args):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _on_sigterm)

    logger.info("daemon started pid=%d interval=%.2fs", os.getpid(), config.poll_interval)
    state_map: dict[str, tuple[State, float]] = {}
    try:
        while True:
            now = time.time()
            try:
                rows = _poll_once(config, state_map, now)
                sorted_rows = sort_rows(rows, now)
                _atomic_write(TSV_PATH, render_tsv(sorted_rows, now))
            except Exception:
                # Don't let a transient libtmux/socket hiccup kill the daemon;
                # log and continue. A genuinely broken setup will log every
                # poll, which RotatingFileHandler caps.
                logger.exception("poll failed")
            time.sleep(config.poll_interval)
    except KeyboardInterrupt:
        logger.info("daemon exiting")
        return 0
    finally:
        _release_singleton()


@dataclass(frozen=True)
class StopResult:
    """Outcome of a stop_daemon call."""

    status: str  # "not_running" | "stopped" | "timeout"
    pid: int | None = None


def stop_daemon(timeout: float = 5.0, poll_interval: float = 0.1) -> StopResult:
    """Send SIGTERM to the running daemon and wait until it exits.

    The daemon's SIGTERM handler raises KeyboardInterrupt, which runs the
    finally block that removes the PID file. We poll for process death
    rather than the file's disappearance — death is the authoritative
    signal, file cleanup is a courtesy.
    """
    if not PID_PATH.exists():
        return StopResult(status="not_running")

    try:
        pid = int(PID_PATH.read_text().strip())
    except (ValueError, OSError):
        return StopResult(status="not_running")

    if not _pid_alive(pid):
        # Stale PID file from a crash. Clean it up; caller can proceed.
        try:
            PID_PATH.unlink()
        except OSError:
            pass
        return StopResult(status="not_running", pid=pid)

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return StopResult(status="not_running", pid=pid)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return StopResult(status="stopped", pid=pid)
        time.sleep(poll_interval)

    return StopResult(status="timeout", pid=pid)


def run_status_once(config: Config) -> str:
    """Single poll + render, return TSV string. Used by `twmux watch status`."""
    state_map: dict[str, tuple[State, float]] = {}
    now = time.time()
    rows = _poll_once(config, state_map, now)
    return render_tsv(sort_rows(rows, now), now)

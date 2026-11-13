"""Watch daemon: poll panes across sockets, classify state, write sorted TSV.

Split into two layers:
- Pure logic (sort, merge, format) — unit-tested in tests/lib/test_watch.py.
- tmux glue (poll loop, pane enumeration, file I/O) — integration-tested
  against a hermetic tmux server in tests/lib/test_watch_poll.py.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import signal
import socket as socket_module
import sys
import time
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from logging.handlers import RotatingFileHandler
from pathlib import Path

from twmux.lib.classify import AgentConfig, classify
from twmux.lib.mark import HOOK_STATES, OPT_AGENT, OPT_CMD, OPT_STATE, OPT_STATE_TS
from twmux.lib.safety import enumerate_all_sockets, get_socket_dir

State = str  # "wait" | "done" | "working" | "idle" | "unknown"

# Lower-numbered states are less actionable; sort DESC so high-priority first.
# done = idle, but finished while out of sight: the user has not seen it yet.
_STATE_PRIORITY = {"wait": 4, "done": 3, "idle": 2, "working": 1, "unknown": 0}


@dataclass(frozen=True)
class AgentRow:
    """One observed agent pane, with its current state and tracking metadata."""

    pane_id: str  # libtmux %N id
    socket: str  # tmux socket name; pane ids are only unique per server
    target: str  # session:window.pane address for tmux commands
    project: str  # basename of pane cwd (display only)
    agent: str  # agent config name (e.g. "claude_code")
    state: State
    state_entered_at: float
    title: str  # pane_title set by the running program (display only)
    last_line: str
    window_id: str = ""  # tmux @N id, for the status-bar icon


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


def observe_state(hook_state: State | None, scraped: State) -> State:
    """Combine the agent's own report with what the screen shows.

    The hook is authoritative — it comes from the agent, not from a regex
    guessing at its TUI — except that a wait prompt on screen always wins:
    some agents show permission prompts without emitting a hook. A hook
    "done" means the agent is back at its prompt: idle.
    """
    if scraped == "wait" or hook_state is None:
        return scraped
    return "idle" if hook_state == "done" else hook_state


@dataclass(frozen=True)
class Track:
    """What the daemon remembers about one agent pane between polls."""

    state: State
    entered_at: float  # when `state` began
    content_hash: str
    stable_since: float  # when the captured content last changed
    hook_ts: str  # the mark these flags refer to
    changed_since_mark: bool  # content changed after the first poll of that mark
    seen_at: float  # last poll that found the pane's window in front of the user


def hook_is_stale(
    hook_state: State, stable_for: float, changed_since_mark: bool, stale_secs: float
) -> bool:
    """Whether the screen contradicts the agent's last mark.

    Hooks miss events: Ctrl-C/Escape fire no Stop hook, a rejected
    permission prompt fires nothing. A working agent redraws its spinner
    every second, so `working` is stale once the screen has been frozen for
    stale_secs. Wait and done screens are static by nature; they are stale
    only if the screen changed after the mark — something happened that no
    hook reported — and has since settled.
    """
    if stable_for < stale_secs:
        return False
    return hook_state == "working" or changed_since_mark


def next_track(
    prev: Track | None,
    pane: PaneInfo,
    scraped: State,
    content_hash: str,
    now: float,
    stale_secs: float,
) -> Track:
    """Fold one poll's observation of a pane into its track."""
    content_changed = prev is not None and prev.content_hash != content_hash
    stable_since = prev.stable_since if prev is not None and not content_changed else now
    same_mark = prev is not None and prev.hook_ts == pane.hook_ts
    changed_since_mark = same_mark and (prev.changed_since_mark or content_changed)

    hook = pane.hook_state
    if hook is not None and hook_is_stale(hook, now - stable_since, changed_since_mark, stale_secs):
        hook = None
    observed = observe_state(hook, scraped)

    prev_state = (prev.state, prev.entered_at) if prev is not None else None
    state, entered_at = merge_state(prev_state, observed, now)
    # A pane first met counts as seen: without history there is no evidence
    # the user missed anything. ponytail: "seen" means visible at a poll, so a
    # visit shorter than poll_interval is missed — upgrade path is a tmux
    # pane-focus-in hook setting a flag the daemon consumes.
    seen_at = now if prev is None or pane.visible else prev.seen_at
    return Track(
        state=state,
        entered_at=entered_at,
        content_hash=content_hash,
        stable_since=stable_since,
        hook_ts=pane.hook_ts,
        changed_since_mark=changed_since_mark,
        seen_at=seen_at,
    )


def display_state(track: Track) -> State:
    """The tracked state, with idle split into done (not seen since the agent
    went idle) and idle (seen)."""
    if track.state == "idle" and track.seen_at < track.entered_at:
        return "done"
    return track.state


# Unit separator: cannot appear in session names or paths and is vanishingly
# rare in pane titles; a line with the wrong field count is dropped.
_SEP = "\x1f"
SNAPSHOT_FORMAT = _SEP.join(
    [
        "#{pane_id}",
        "#{pane_current_command}",
        "#{pane_title}",
        "#{pane_current_path}",
        "#{session_name}",
        "#{window_id}",
        "#{window_index}",
        "#{pane_index}",
        "#{window_active}",
        "#{session_attached}",
        f"#{{{OPT_STATE}}}",
        f"#{{{OPT_STATE_TS}}}",
        f"#{{{OPT_CMD}}}",
        f"#{{{OPT_AGENT}}}",
    ]
)
_SNAPSHOT_FIELDS = SNAPSHOT_FORMAT.count(_SEP) + 1


@dataclass(frozen=True)
class PaneInfo:
    """One pane as reported by a single `list-panes -a -F SNAPSHOT_FORMAT`."""

    pane_id: str
    command: str
    title: str
    path: str
    target: str  # session:window.pane address for tmux commands
    window_id: str
    visible: bool  # its window is the active one of an attached session
    hook_state: State | None = None  # from `twmux mark`, None when absent or stale
    hook_ts: str = ""  # identifies the mark: a new mark resets staleness tracking
    hook_agent: str = ""


def parse_snapshot(lines: list[str]) -> list[PaneInfo]:
    """Parse list-panes output produced with SNAPSHOT_FORMAT."""
    panes = []
    for line in lines:
        fields = line.split(_SEP)
        if len(fields) != _SNAPSHOT_FIELDS:
            continue
        (pane_id, command, title, path, session, window_id, window_index, pane_index,
         window_active, session_attached,
         hook_state, hook_ts, hook_cmd, hook_agent) = fields  # fmt: skip
        # A mark made under another command belongs to an agent that has
        # since exited from this pane (e.g. "2.1.139" -> "bash").
        valid_hook = hook_state in HOOK_STATES and hook_cmd == command
        panes.append(
            PaneInfo(
                pane_id=pane_id,
                command=command,
                title=title,
                path=path,
                target=f"{session}:{window_index}.{pane_index}",
                window_id=window_id,
                visible=window_active == "1" and session_attached not in ("", "0"),
                hook_state=hook_state if valid_hook else None,
                hook_ts=hook_ts if valid_hook else "",
                hook_agent=hook_agent if valid_hook else "",
            )
        )
    return panes


def render_json(rows: list[AgentRow]) -> str:
    """Serialize rows for tools (`twmux watch next`, scripts): unlike the TSV,
    it carries the socket, pane id and epoch timestamps."""
    return json.dumps(
        [
            {
                "socket": r.socket,
                "pane_id": r.pane_id,
                "target": r.target,
                "project": r.project,
                "agent": r.agent,
                "state": r.state,
                "state_entered_at": r.state_entered_at,
                "title": r.title,
            }
            for r in rows
        ]
    )


def rows_from_json(text: str) -> list[AgentRow]:
    return [AgentRow(**entry, last_line="") for entry in json.loads(text)]


def pick_next(
    rows: list[AgentRow],
    socket: str,
    current_pane: str,
    cursor: dict | None,
) -> tuple[AgentRow | None, dict | None]:
    """Choose the agent `twmux watch next` jumps to.

    Candidates are wait/done agents on the caller's server (switch-client
    cannot reach other servers), except the pane the caller is in, newest
    state change first. Repeated calls walk to older candidates; the walk
    restarts at the newest when the newest changes (another agent finished).
    Returns (row, cursor to pass to the next call).
    """
    candidates = sorted(
        (
            r
            for r in rows
            if r.state in ("wait", "done") and r.socket == socket and r.pane_id != current_pane
        ),
        key=lambda r: (-r.state_entered_at, r.pane_id),
    )
    if not candidates:
        return None, None
    newest = candidates[0]
    head = f"{newest.pane_id}@{newest.state_entered_at}"
    index = 0
    if cursor is not None and cursor.get("head") == head:
        index = (cursor.get("index", 0) + 1) % len(candidates)
    return candidates[index], {"head": head, "index": index}


def window_icons(rows: list[AgentRow], icons: dict[str, str]) -> dict[tuple[str, str], str]:
    """Status-bar icon per (socket, window id): that of the window's most
    urgent agent. Windows whose agents have no icon (idle) are left out."""
    best: dict[tuple[str, str], AgentRow] = {}
    for r in rows:
        key = (r.socket, r.window_id)
        if key not in best or state_priority(r.state) > state_priority(best[key].state):
            best[key] = r
    return {key: icons[r.state] for key, r in best.items() if icons.get(r.state)}


# ----------------------------------------------------------------------------
# Config loading
# ----------------------------------------------------------------------------


# Icons the daemon writes to the window option @twmux_status; idle and
# unknown windows get none. An empty string disables the icon for a state.
DEFAULT_STATUS_ICONS = {"wait": "💬", "done": "✅", "working": "🤖"}


@dataclass(frozen=True)
class Config:
    poll_interval: float
    agents: list[AgentConfig]
    hook_stale_secs: float = 10.0
    status_icons: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_STATUS_ICONS))


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
    hook_stale_secs = float(data.pop("hook_stale_secs", 10.0))
    status_icons = {**DEFAULT_STATUS_ICONS, **data.pop("status_icons", {})}
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
    return Config(
        poll_interval=poll_interval,
        agents=agents,
        hook_stale_secs=hook_stale_secs,
        status_icons=status_icons,
    )


# ----------------------------------------------------------------------------
# tmux glue — poll_server is integration-tested in tests/lib/test_watch_poll.py
# ----------------------------------------------------------------------------


CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "twmux"
TSV_PATH = CACHE_DIR / "agents.tsv"
JSON_PATH = CACHE_DIR / "agents.json"
NEXT_CURSOR_PATH = CACHE_DIR / "next.json"
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


def _match_agent(
    cmd: str, title: str, agents: list[AgentConfig], content: str = ""
) -> AgentConfig | None:
    """Find first agent config that matches this pane.

    An agent matches if either its cmd_match regex hits the pane's current
    command OR its title_match regex hits the pane title. If the matching
    config also has content_match, the pane's captured text must satisfy
    that regex too — otherwise we skip to the next candidate.
    """
    for cfg in agents:
        if _hits_cmd_or_title(cfg, cmd, title):
            if cfg.content_match and not re.search(cfg.content_match, content, re.MULTILINE):
                continue
            return cfg
    return None


def _hits_cmd_or_title(cfg: AgentConfig, cmd: str, title: str) -> bool:
    return bool(
        (cfg.cmd_match and re.search(cfg.cmd_match, cmd))
        or (cfg.title_match and re.search(cfg.title_match, title))
    )


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


StateKey = tuple[str, str]  # (socket name, pane id)


def poll_server(
    server,
    config: Config,
    state_map: dict[StateKey, Track],
    now: float,
) -> list[AgentRow]:
    """Poll one tmux server: one list-panes for every pane, then capture-pane
    only for panes that look like agents. Updates state_map in place for the
    panes seen here; pruning vanished panes is the caller's job, because the
    map spans all sockets."""
    socket = server.socket_name
    snapshot = parse_snapshot(server.cmd("list-panes", "-a", "-F", SNAPSHOT_FORMAT).stdout)

    rows: list[AgentRow] = []
    for pane in snapshot:
        # Capture only panes that are agents by their own mark or that some
        # config claims by command or title: capturing is the expensive part,
        # and most panes are plain shells and editors.
        claimed = any(_hits_cmd_or_title(c, pane.command, pane.title) for c in config.agents)
        if not claimed and pane.hook_state is None:
            continue
        captured = server.cmd("capture-pane", "-p", "-t", pane.pane_id).stdout
        text = "\n".join(captured)
        cfg = _match_agent(pane.command, pane.title, config.agents, content=text)
        if cfg is None and pane.hook_state is None:
            continue

        scraped = classify(text, cfg) if cfg else "unknown"
        key = (socket, pane.pane_id)
        track = next_track(
            state_map.get(key),
            pane,
            scraped,
            hashlib.md5(text.encode()).hexdigest(),
            now,
            config.hook_stale_secs,
        )
        state_map[key] = track
        rows.append(
            AgentRow(
                pane_id=pane.pane_id,
                socket=socket,
                target=pane.target,
                project=_basename_cwd(pane.path),
                agent=cfg.name if cfg else (pane.hook_agent or "agent"),
                state=display_state(track),
                state_entered_at=track.entered_at,
                title=pane.title,
                last_line=_last_nonblank_line(captured),
                window_id=pane.window_id,
            )
        )
    return rows


WindowIcons = dict[tuple[str, str], str]  # (socket, window id) -> icon


def sync_window_status(server, desired: WindowIcons, written: WindowIcons) -> None:
    """Make this server's @twmux_status window options match `desired`.

    `written` remembers what the daemon set (across all servers), so only
    changes cost a tmux call and windows that calmed down get unset.
    """
    socket = server.socket_name
    for key, icon in desired.items():
        if key[0] == socket and written.get(key) != icon:
            server.cmd("set-option", "-w", "-t", key[1], "@twmux_status", icon)
            written[key] = icon
    for key in [k for k in written if k[0] == socket and k not in desired]:
        # The window may be gone already; then there is nothing to unset.
        server.cmd("set-option", "-w", "-u", "-t", key[1], "@twmux_status")
        del written[key]


def _poll_once(
    config: Config,
    state_map: dict[StateKey, Track],
    now: float,
    written_icons: WindowIcons | None = None,
) -> list[AgentRow]:
    """Poll every live socket; prune state of panes that vanished.

    With written_icons (the daemon), also update the status-bar options;
    without (`watch status`), only read.
    """
    from libtmux import Server

    rows: list[AgentRow] = []
    for sock in enumerate_all_sockets():
        if not _socket_alive(sock):
            continue
        try:
            server = Server(socket_name=sock)
            server_rows = poll_server(server, config, state_map, now)
            rows.extend(server_rows)
            if written_icons is not None:
                icons = window_icons(server_rows, config.status_icons)
                sync_window_status(server, icons, written_icons)
        except Exception:
            # A server dying mid-poll must not hide the agents on the others.
            continue

    seen = {(r.socket, r.pane_id) for r in rows}
    for stale_key in [k for k in state_map if k not in seen]:
        state_map.pop(stale_key, None)
    return rows


def _clear_window_status(written: WindowIcons) -> None:
    """Best effort on exit: a stopped daemon must not leave icons behind."""
    from libtmux import Server

    for sock in {k[0] for k in written}:
        try:
            sync_window_status(Server(socket_name=sock), {}, written)
        except Exception:
            pass


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
    state_map: dict[StateKey, Track] = {}
    written_icons: WindowIcons = {}
    try:
        while True:
            now = time.time()
            try:
                rows = _poll_once(config, state_map, now, written_icons)
                sorted_rows = sort_rows(rows, now)
                _atomic_write(TSV_PATH, render_tsv(sorted_rows, now))
                _atomic_write(JSON_PATH, render_json(sorted_rows))
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
        _clear_window_status(written_icons)
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
    state_map: dict[StateKey, Track] = {}
    now = time.time()
    rows = _poll_once(config, state_map, now)
    return render_tsv(sort_rows(rows, now), now)


def jump_next(socket_path: str) -> AgentRow | None:
    """Switch the caller's tmux client to the agent pick_next chooses.

    Reads the daemon's agents.json, so it is as fresh as the last poll.
    Returns the row jumped to, or None when there is nothing to jump to.
    Raises FileNotFoundError when the daemon has never written agents.json.
    """
    from libtmux import Server

    server = Server(socket_path=socket_path)
    rows = rows_from_json(JSON_PATH.read_text())
    # The client that pressed the key is the most recently active one. Ask
    # tmux rather than $TMUX_PANE: run-shell jobs inherit the server's global
    # environment, whose TMUX_PANE is whatever pane started the server.
    clients = server.cmd("list-clients", "-F", "#{client_activity} #{client_name} #{pane_id}")
    if not clients.stdout:
        raise RuntimeError("no tmux client attached")
    _, client, current_pane = max(line.split(" ") for line in clients.stdout)
    try:
        cursor = json.loads(NEXT_CURSOR_PATH.read_text())
    except (OSError, ValueError):
        cursor = None

    socket = os.path.basename(socket_path)
    row, cursor = pick_next(rows, socket, current_pane, cursor)
    if row is None:
        return None

    pane = row.pane_id
    result = server.cmd(
        "switch-client",
        "-c",
        client,
        "-t",
        pane,
        ";",
        "select-window",
        "-t",
        pane,
        ";",
        "select-pane",
        "-t",
        pane,
    )
    if result.stderr:
        raise RuntimeError("; ".join(result.stderr))
    _atomic_write(NEXT_CURSOR_PATH, json.dumps(cursor))
    return row

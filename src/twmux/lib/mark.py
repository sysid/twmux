"""Agent-hook state channel: agents report their own state into tmux.

An agent's lifecycle hook runs `twmux mark <state>` inside the agent's pane.
The state is stored as tmux *pane options*, so it lives and dies with the
pane — no files, no cleanup, no pane-id recycling to detect. The watch
daemon reads these options in its one list-panes snapshot per server.

Options written on the pane:
    @twmux_state     working | wait | done
    @twmux_state_ts  epoch seconds of the mark
    @twmux_cmd       pane_current_command at mark time; when the pane later
                     runs something else, the agent has exited and the mark
                     is stale
    @twmux_agent     optional agent name, for panes no regex identifies
"""

from __future__ import annotations

import os
import time

HOOK_STATES = ("working", "wait", "done")

OPT_STATE = "@twmux_state"
OPT_STATE_TS = "@twmux_state_ts"
OPT_CMD = "@twmux_cmd"
OPT_AGENT = "@twmux_agent"
ALL_OPTIONS = (OPT_STATE, OPT_STATE_TS, OPT_CMD, OPT_AGENT)


def hook_pane_from_env(env: dict[str, str] | os._Environ = os.environ) -> tuple[str, str] | None:
    """(socket path, pane id) of the pane this process runs in, or None.

    Hooks inherit $TMUX ("<socket path>,<server pid>,<session>") and
    $TMUX_PANE from the agent, which identifies the pane exactly — no
    process-tree walking needed.
    """
    socket_path = env.get("TMUX", "").split(",")[0]
    pane_id = env.get("TMUX_PANE", "")
    if not socket_path or not pane_id:
        return None
    return socket_path, pane_id


def opted_out(env: dict[str, str] | os._Environ = os.environ) -> bool:
    """True when this hook must not touch the pane state.

    TWMUX_NO_MARK: set on nested agents so only the outer agent drives the
    pane. CLAUDE_JOB_DIR: Claude background workers share their supervisor's
    pane and would overwrite the state of the session in front of the user.
    """
    return bool(env.get("TWMUX_NO_MARK") or env.get("CLAUDE_JOB_DIR"))


def mark_pane(server, pane_id: str, state: str, agent: str = "") -> None:
    """Write (or for state "clear", remove) the hook options on the pane.

    One tmux invocation with ";"-separated commands: a hook runs on every
    tool call, so it must be cheap, and the daemon never sees a half-written
    mark.
    """
    args: list[str] = []
    if state == "clear":
        for name in ALL_OPTIONS:
            args += ["set-option", "-p", "-u", "-t", pane_id, name, ";"]
    else:
        args += ["set-option", "-p", "-t", pane_id, OPT_STATE, state, ";"]
        args += ["set-option", "-p", "-t", pane_id, OPT_STATE_TS, str(int(time.time())), ";"]
        args += ["set-option", "-pF", "-t", pane_id, OPT_CMD, "#{pane_current_command}", ";"]
        if agent:
            args += ["set-option", "-p", "-t", pane_id, OPT_AGENT, agent, ";"]
    result = server.cmd(*args[:-1])
    if result.stderr:
        raise RuntimeError("; ".join(result.stderr))

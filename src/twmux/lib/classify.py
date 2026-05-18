"""Pane-state classification for known agents.

Pure logic: takes captured pane text + per-agent regex config, returns the
state string. No libtmux, no I/O.

States:
    wait     — agent is blocked on user input (permission prompt, confirm, etc.)
    working  — agent is actively processing
    idle     — agent is at its prompt, no current activity
    unknown  — none of the configured patterns matched

Priority when multiple regexes match: wait > working > idle.
"""

import re
from dataclasses import dataclass

State = str  # "wait" | "working" | "idle" | "unknown"


@dataclass(frozen=True)
class AgentConfig:
    """Per-agent classification config, loaded from agents.toml.

    A pane matches this agent if EITHER cmd_match hits the pane's current
    command OR title_match hits the pane's title. At least one must be set.

    If content_match is set, the pane content (captured text) must ALSO match
    this regex — this prevents false positives when cmd_match is broad
    (e.g. "node" matches any node process, but only Copilot has the
    distinctive footer).
    """

    name: str
    cmd_match: str | None = None
    title_match: str | None = None
    content_match: str | None = None
    re_working: str | None = None
    re_wait: str | None = None
    re_idle: str | None = None


def classify(captured: str, cfg: AgentConfig) -> State:
    """Classify pane state by matching configured regexes against captured text.

    Each regex is applied with re.MULTILINE so anchors like ^/$ work line-wise.
    Wait wins over working wins over idle — the most actionable state takes
    precedence. Missing patterns are skipped (None means "no signal for this
    state").
    """
    if not captured:
        return "unknown"

    if cfg.re_wait and re.search(cfg.re_wait, captured, re.MULTILINE):
        return "wait"
    if cfg.re_working and re.search(cfg.re_working, captured, re.MULTILINE):
        return "working"
    if cfg.re_idle and re.search(cfg.re_idle, captured, re.MULTILINE):
        return "idle"
    return "unknown"

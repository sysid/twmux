"""Tests for classify module: per-agent pane state classification."""

from twmux.lib.classify import AgentConfig, classify
from twmux.lib.watch import _match_agent

CLAUDE = AgentConfig(
    name="claude_code",
    cmd_match="claude",
    re_working="esc to interrupt",
    re_wait=r"do you want to proceed|Do you trust",
    re_idle=r"^│ > ",
)

AIDER = AgentConfig(
    name="aider",
    cmd_match="aider",
    re_working=None,
    re_wait=r"^\? ",
    re_idle=r"^> ",
)


def test_claude_working_when_spinner_visible():
    pane = "Reading files...\n  ⏵ Compiling (esc to interrupt)\n"
    assert classify(pane, CLAUDE) == "working"


def test_claude_wait_on_permission_prompt():
    pane = "Edit src/foo.py?\ndo you want to proceed?\n  > 1. Yes\n  2. No\n"
    assert classify(pane, CLAUDE) == "wait"


def test_claude_wait_on_trust_prompt():
    pane = "Do you trust the files in this folder?\n  > 1. Yes\n"
    assert classify(pane, CLAUDE) == "wait"


def test_claude_idle_at_prompt():
    pane = "All done.\n\n╭─ Tip ──╮\n│ > \n"
    assert classify(pane, CLAUDE) == "idle"


def test_aider_wait_on_confirm():
    pane = "Edit src/foo.py?\n? "
    assert classify(pane, AIDER) == "wait"


def test_aider_idle_at_prompt():
    pane = "Output complete.\n\n> \n"
    assert classify(pane, AIDER) == "idle"


def test_unknown_when_nothing_matches():
    pane = "some random terminal output\nnothing matches\n"
    assert classify(pane, CLAUDE) == "unknown"


def test_wait_wins_over_working_when_both_match():
    """If both wait and working patterns match, wait is the actionable state."""
    pane = "Compiling (esc to interrupt)\ndo you want to proceed?\n"
    assert classify(pane, CLAUDE) == "wait"


def test_wait_wins_over_idle_when_both_match():
    pane = "do you want to proceed?\n│ > \n"
    assert classify(pane, CLAUDE) == "wait"


def test_missing_pattern_is_skipped_not_error():
    """Aider config has no re_working; classify must not crash."""
    pane = "some text"
    assert classify(pane, AIDER) == "unknown"


def test_empty_capture_is_unknown():
    assert classify("", CLAUDE) == "unknown"


def test_match_agent_by_cmd_only():
    cfg = AgentConfig(name="aider", cmd_match="aider")
    assert _match_agent("aider", "some title", [cfg]) is cfg


def test_match_agent_by_title_only():
    """Claude Code's command is a version string; the title is the reliable
    signal. ✳ is the spinner character CC uses as a title prefix."""
    cfg = AgentConfig(name="claude_code", title_match=r"^✳ ")
    assert _match_agent("2.1.139", "✳ Claude Code", [cfg]) is cfg


def test_match_agent_no_match_returns_none():
    cfg = AgentConfig(name="claude_code", title_match=r"^✳ ")
    assert _match_agent("bash", "regular terminal", [cfg]) is None


def test_match_agent_first_match_wins():
    a = AgentConfig(name="a", cmd_match="foo")
    b = AgentConfig(name="b", cmd_match="foo")
    assert _match_agent("foo", "", [a, b]) is a


def test_match_agent_either_signal_matches():
    """OR semantics — title hits even when cmd does not."""
    cfg = AgentConfig(name="claude_code", cmd_match="claude", title_match=r"^✳ ")
    assert _match_agent("2.1.139", "✳ task", [cfg]) is cfg
    assert _match_agent("claude", "random", [cfg]) is cfg


def test_idle_matches_prompt_line_anywhere_in_capture():
    """Idle pattern is line-anchored (MULTILINE ^). The prompt line can
    appear anywhere in the capture; if working/wait don't match more
    strongly, the pane is considered idle."""
    pane = "│ > \nsome later output\n"
    assert classify(pane, CLAUDE) == "idle"

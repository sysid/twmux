"""Tests for classify module: per-agent pane state classification."""

from twmux.lib.classify import AgentConfig, classify
from twmux.lib.watch import _match_agent

CLAUDE = AgentConfig(
    name="claude_code",
    cmd_match="claude",
    # Working: legacy "esc to interrupt" hint OR a spinner-anchored status
    # line. The glyph anchor at line-start prevents false positives from
    # plain text that happens to contain "… (5s)" (code comments, chat
    # transcripts, doc snippets). The character class covers the Dingbats
    # asterisk/sparkle/snowflake family (U+2726–U+274B = ✦…❋), since CC
    # cycles through many glyphs (✱ ✲ ✳ ✶ ✸ ✺ ✻ ✼ ✽ ✪ ❋ …);
    # narrower ranges keep getting bitten by an unlisted glyph. The range
    # stops at U+274B so check marks / cross marks (U+274C onward) stay
    # excluded.
    re_working=r"esc to interrupt|^[✦-❋] .+… ?\(\d+[ms]",
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


def test_claude_working_via_elapsed_time_status():
    """Newer auto/YOLO-mode CC drops "esc to interrupt" and prints only the
    elapsed-time spinner line. The empty input prompt (`│ > `) is still
    visible on screen — re_working must win over re_idle here, otherwise
    actively running panes are mis-tagged idle."""
    pane = (
        "✽ Upgrading rstest… (2m 16s · ↓ 2.5k tokens · thought for 3s)\n"
        "─────\n"
        "│ > \n"
    )
    assert classify(pane, CLAUDE) == "working"


def test_claude_working_matches_multiple_spinner_glyphs():
    """CC cycles through many star/asterisk glyphs from the Dingbats block
    on the body status line — observed in the wild: ✱ ✲ ✳ ✶ ✸ ✺ ✻ ✼ ✽
    ✪. Notably ✳ is the same glyph CC uses as its idle title prefix, but
    it can also appear in body spinner cycling. The regex must accept
    any glyph in the Dingbats star/asterisk range, not just a hand-picked
    subset — otherwise running panes whose spinner happens to land on an
    unlisted glyph are mis-tagged idle."""
    # ❋ (U+274B) is one CC actually uses — caught in the wild on
    # `❋ Skedaddling… (50s · ↓ …)`. Earlier hand-picked subsets and
    # narrower ranges (U+2726–U+2743) both missed it. The range now
    # extends to U+274B so the full asterisk/sparkle/snowflake family
    # is covered.
    for glyph in ["✱", "✲", "✳", "✶", "✸", "✺", "✻", "✼", "✽", "✪", "❋"]:
        pane = f"{glyph} Cultivating… (21s · thinking more with xhigh effort)\n│ > \n"
        assert classify(pane, CLAUDE) == "working", f"failed for glyph {glyph!r}"


def test_claude_idle_when_ellipsis_time_appears_without_spinner():
    """Plain text containing '… (5s)' (e.g. a doc snippet, transcript, or
    code comment quoted on screen) must NOT trigger working. The spinner
    glyph anchor at line-start is what distinguishes a real CC status
    line from incidental text that mentions an elapsed time."""
    pane = (
        "Some explanation about how the build took… (5s · really fast)\n"
        "More text below.\n"
        "│ > \n"
    )
    assert classify(pane, CLAUDE) == "idle"


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
    signal. ✳ is the title prefix CC uses when idle; while processing the
    prefix cycles through braille spinner glyphs (U+2800–U+28FF). The
    example config's regex covers both so working panes also match."""
    cfg = AgentConfig(name="claude_code", title_match=r"^[✳⠀-⣿] ")
    assert _match_agent("2.1.139", "✳ Claude Code", [cfg]) is cfg
    # Spinner-prefixed titles (working state) must also match, otherwise
    # active sessions are invisible in the popup.
    assert _match_agent("2.1.143", "⠐ Fix bug", [cfg]) is cfg
    assert _match_agent("2.1.143", "⡀ Compiling", [cfg]) is cfg


def test_match_agent_no_match_returns_none():
    cfg = AgentConfig(name="claude_code", title_match=r"^[✳⠀-⣿] ")
    assert _match_agent("bash", "regular terminal", [cfg]) is None


def test_match_agent_first_match_wins():
    a = AgentConfig(name="a", cmd_match="foo")
    b = AgentConfig(name="b", cmd_match="foo")
    assert _match_agent("foo", "", [a, b]) is a


def test_match_agent_either_signal_matches():
    """OR semantics — title hits even when cmd does not."""
    cfg = AgentConfig(name="claude_code", cmd_match="claude", title_match=r"^[✳⠀-⣿] ")
    assert _match_agent("2.1.139", "✳ task", [cfg]) is cfg
    assert _match_agent("claude", "random", [cfg]) is cfg


def test_match_agent_content_match_gates_cmd():
    """content_match adds an AND gate — cmd_match hits but content must also match."""
    cfg = AgentConfig(name="copilot_cli", cmd_match="^node$", content_match=r"/ commands · \? help")
    # node + copilot footer → match
    assert _match_agent("node", "Fix bug", [cfg], content="stuff\n/ commands · ? help\n") is cfg
    # node but no footer → no match
    assert _match_agent("node", "Fix bug", [cfg], content="Express listening on :3000\n") is None
    # non-node → no match even with footer
    assert _match_agent("python", "x", [cfg], content="/ commands · ? help\n") is None


def test_match_agent_content_match_skips_to_next_candidate():
    """When content_match fails, try the next agent in the list."""
    copilot = AgentConfig(name="copilot_cli", cmd_match="^node$", content_match=r"/ commands · \? help")
    generic_node = AgentConfig(name="generic_node", cmd_match="node")
    # No footer → copilot skipped, generic_node matches
    assert _match_agent("node", "", [copilot, generic_node], content="server ready") is generic_node


def test_idle_matches_prompt_line_anywhere_in_capture():
    """Idle pattern is line-anchored (MULTILINE ^). The prompt line can
    appear anywhere in the capture; if working/wait don't match more
    strongly, the pane is considered idle."""
    pane = "│ > \nsome later output\n"
    assert classify(pane, CLAUDE) == "idle"


# -- Copilot CLI classification ------------------------------------------------

COPILOT = AgentConfig(
    name="copilot_cli",
    cmd_match="^node$",
    content_match=r"/ commands · \? help",
    re_idle=r"^❯\s*$",
    re_working=r"Esc to cancel",
    re_wait=None,
)


def test_copilot_idle_at_prompt():
    pane = (
        " ~/dev/project [⎇ main]\n"
        "────────────────────────────────\n"
        "❯\n"
        "────────────────────────────────\n"
        " / commands · ? help                 Claude Opus 4.6\n"
    )
    assert classify(pane, COPILOT) == "idle"


def test_copilot_working_during_tool_execution():
    pane = (
        "● Running tests (Esc to cancel · 2.1 KiB)\n"
        " ~/dev/project [⎇ main]\n"
        "────────────────────────────────\n"
        "❯\n"
        "────────────────────────────────\n"
        " / commands · ? help                 Claude Opus 4.6\n"
    )
    assert classify(pane, COPILOT) == "working"


def test_copilot_wait_on_numbered_choices():
    """Without re_wait, copilot at prompt classifies as idle even when
    ask_user choices are visible — idle already means 'needs attention'."""
    pane = (
        "? What database should I use?\n"
        "1. PostgreSQL (Recommended)\n"
        "2. MySQL\n"
        "────────────────────────────────\n"
        "❯\n"
        "────────────────────────────────\n"
        " / commands · ? help                 Claude Opus 4.6\n"
    )
    assert classify(pane, COPILOT) == "idle"

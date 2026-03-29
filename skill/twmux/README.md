# twmux

## Testing

The twmux skill is an operational skill — it teaches Claude how to use a CLI tool that requires a
running tmux server.

Option A: Manual smoke test

Start a new Claude Code session, give it tasks that should trigger the skill, and verify:

"Run pytest in a tmux session and capture the output"
"Start a Python REPL, compute fibonacci(10), and tell me the result"
"Create two tmux sessions and move a pane between them"

Check: Does it use --json? Does it check ok? Does it use PYTHON_BASIC_REPL=1? Does it tell you the monitor command?

Option B: Description triggering eval (the skill-creator CAN do this)

Test whether the skill triggers on the right prompts. This runs claude -p with various prompts and
checks if the skill was invoked. This part doesn't need tmux — it just tests whether the
description is good enough to make Claude reach for
the skill.

Option C: Integration tests in twmux's own test suite

The 64 pytest tests we wrote already validate the CLI contract. The skill is documentation — if the
CLI works and the docs match, the skill works.

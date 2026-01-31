"""Tests for execution module."""

import time

from twmux.lib.execution import ExecResult, generate_markers, parse_output, wrap_command


def test_exec_result():
    result = ExecResult(output="hello\nworld", exit_code=0, timed_out=False)
    assert result.output == "hello\nworld"
    assert result.exit_code == 0
    assert result.timed_out is False


def test_exec_result_timeout():
    result = ExecResult(output="", exit_code=-1, timed_out=True)
    assert result.exit_code == -1
    assert result.timed_out is True


def test_generate_markers_unique():
    m1_start, m1_end = generate_markers()
    m2_start, m2_end = generate_markers()

    # Different calls produce different markers
    assert m1_start != m2_start
    assert m1_end != m2_end

    # Markers have expected format
    assert m1_start.startswith("__TWMUX_START_")
    assert m1_end.startswith("__TWMUX_END_")


def test_wrap_command():
    start, end = "__START__", "__END__"
    wrapped = wrap_command("ls -la", start, end)

    assert start in wrapped
    assert end in wrapped
    assert "ls -la" in wrapped
    assert "$?" in wrapped  # Exit code capture


def test_parse_output_success():
    start, end = "__START__", "__END__"
    captured = f"""some prompt
{start}
file1.txt
file2.txt
{end}:0
$ """

    result = parse_output(captured, start, end)
    assert result.exit_code == 0
    assert "file1.txt" in result.output
    assert "file2.txt" in result.output
    assert result.timed_out is False


def test_parse_output_nonzero_exit():
    start, end = "__START__", "__END__"
    captured = f"""{start}
ls: cannot access 'nonexistent': No such file or directory
{end}:2
"""

    result = parse_output(captured, start, end)
    assert result.exit_code == 2
    assert "No such file or directory" in result.output


def test_parse_output_missing_markers():
    result = parse_output("random output", "__START__", "__END__")
    assert result.exit_code == -1
    assert result.timed_out is False


def test_parse_output_marker_in_command_line():
    """Bug fix: START marker appears twice - in command line and as echo output.

    The captured pane shows:
    1. The command line: echo __START__; { ls; } 2>&1; echo __END__:$?
    2. The START marker output: __START__
    3. The actual command output
    4. The END marker output: __END__:0

    parse_output must use the LAST occurrence of START marker.
    """
    start, end = "__START__", "__END__"
    # Simulates real pane capture: command line + echo outputs
    captured = f"""echo {start}; {{ ls; }} 2>&1; echo {end}:$?
{start}
file1.txt
file2.txt
{end}:0
$ """

    result = parse_output(captured, start, end)
    assert result.exit_code == 0
    # Output should be ONLY the command output, not the command line
    assert result.output == "file1.txt\nfile2.txt"
    assert "echo" not in result.output
    assert "{" not in result.output


def test_execute_simple_command(pane):
    """Execute simple command and capture exit code."""
    from twmux.lib.execution import execute

    time.sleep(0.3)  # Let shell settle

    result = execute(pane, "echo hello", timeout=5.0)
    assert result.exit_code == 0
    assert "hello" in result.output
    assert result.timed_out is False


def test_execute_failing_command(pane):
    """Command with non-zero exit code."""
    from twmux.lib.execution import execute

    time.sleep(0.3)

    result = execute(pane, "ls /nonexistent_path_12345", timeout=5.0)
    assert result.exit_code != 0
    assert result.timed_out is False


def test_execute_timeout(pane):
    """Long-running command should timeout."""
    from twmux.lib.execution import execute

    time.sleep(0.3)

    result = execute(pane, "sleep 10", timeout=0.5)
    assert result.timed_out is True
    assert result.exit_code == -1

    # Clean up
    pane.send_keys("C-c", enter=False)
    time.sleep(0.2)

"""Marker-based command execution with exit code capture."""

import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from libtmux import Pane

# Progressive capture expansion levels
EXPANSION_LEVELS: list[int | None] = [100, 500, 2000, None]


@dataclass
class ExecResult:
    """Result of command execution."""

    output: str
    exit_code: int  # -1 = timeout/markers not found
    timed_out: bool


def generate_markers() -> tuple[str, str]:
    """Generate unique start and end markers.

    Uses PID + nanosecond timestamp for uniqueness.
    """
    unique_id = f"{os.getpid()}_{time.time_ns()}"
    start_marker = f"__TWMUX_START_{unique_id}__"
    end_marker = f"__TWMUX_END_{unique_id}__"
    return start_marker, end_marker


def wrap_command(cmd: str, start_marker: str, end_marker: str) -> str:
    """Wrap command with markers to capture output and exit code."""
    return f"echo {start_marker}; {{ {cmd}; }} 2>&1; echo {end_marker}:$?"


def parse_output(captured: str, start_marker: str, end_marker: str) -> ExecResult:
    """Parse captured output to extract command output and exit code.

    Returns:
        ExecResult with parsed output and exit code, or exit_code=-1 if markers not found
    """
    if start_marker not in captured:
        return ExecResult(output=captured, exit_code=-1, timed_out=False)

    # Find end marker with exit code
    end_pattern = re.escape(end_marker) + r":(\d+)"
    end_match = re.search(end_pattern, captured)

    if not end_match:
        return ExecResult(output=captured, exit_code=-1, timed_out=False)

    exit_code = int(end_match.group(1))

    # Extract output between markers (use rfind to get LAST occurrence,
    # since the marker also appears in the echoed command line)
    start_idx = captured.rfind(start_marker) + len(start_marker)
    end_idx = end_match.start()

    output = captured[start_idx:end_idx].strip()

    return ExecResult(output=output, exit_code=exit_code, timed_out=False)


def execute(
    pane: "Pane",
    cmd: str,
    timeout: float = 30.0,
    poll_interval: float = 0.2,
) -> ExecResult:
    """Execute command and capture output with exit code.

    Uses unique markers to reliably capture command output and exit code.
    Polls with progressive expansion to handle long outputs efficiently.

    Args:
        pane: libtmux Pane object
        cmd: Command to execute
        timeout: Maximum seconds to wait for completion
        poll_interval: Seconds between polls

    Returns:
        ExecResult with output, exit code, and timeout status
    """
    start_marker, end_marker = generate_markers()
    wrapped = wrap_command(cmd, start_marker, end_marker)

    # Send wrapped command
    pane.send_keys(wrapped, enter=True)

    start_time = time.monotonic()

    while True:
        elapsed = time.monotonic() - start_time
        if elapsed >= timeout:
            return ExecResult(output="", exit_code=-1, timed_out=True)

        # Try progressive expansion levels
        for lines in EXPANSION_LEVELS:
            if lines is None:
                captured = "\n".join(pane.capture_pane())
            else:
                captured = "\n".join(pane.capture_pane(start=-lines))

            # Check if end marker present
            end_pattern = re.escape(end_marker) + r":(\d+)"
            if re.search(end_pattern, captured):
                # Verify start marker also present
                if start_marker in captured:
                    return parse_output(captured, start_marker, end_marker)
                # End found but not start - need more lines
                continue
            else:
                # End not found - wait and poll again
                break

        time.sleep(poll_interval)

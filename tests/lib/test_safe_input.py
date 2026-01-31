"""Tests for safe_input module."""

import time

from twmux.lib.safe_input import SendResult, WaitResult


def test_send_result_success():
    result = SendResult(success=True, attempts=1)
    assert result.success is True
    assert result.attempts == 1


def test_send_result_failure():
    result = SendResult(success=False, attempts=3)
    assert result.success is False
    assert result.attempts == 3


def test_wait_result():
    result = WaitResult(idle=True, elapsed=1.5)
    assert result.idle is True
    assert result.elapsed == 1.5


def test_wait_for_idle_already_idle(pane):
    """Pane with no activity should return idle immediately."""
    from twmux.lib.safe_input import wait_for_idle

    # Give shell time to settle
    time.sleep(0.3)

    result = wait_for_idle(pane, poll_interval=0.1, stable_count=2, timeout=5.0)
    assert result.idle is True
    assert result.elapsed < 2.0


def test_wait_for_idle_timeout(pane):
    """Continuous output should cause timeout."""
    from twmux.lib.safe_input import wait_for_idle

    # Ensure clean state - interrupt any running command and wait for prompt
    pane.send_keys("C-c", enter=False)
    time.sleep(0.3)

    # Use python for reliable cross-shell continuous output
    pane.send_keys(
        "python3 -c \"import time; i=0; exec('while True:\\n print(i)\\n i+=1')\"",
        enter=True,
    )
    # Wait for command to start producing output
    time.sleep(0.5)

    result = wait_for_idle(pane, poll_interval=0.05, stable_count=3, timeout=0.5)
    assert result.idle is False

    # Clean up
    pane.send_keys("C-c", enter=False)


def test_send_safe_success(pane):
    """Text should be sent and Enter verified."""
    from twmux.lib.safe_input import send_safe

    # Give shell time to settle
    time.sleep(0.3)

    result = send_safe(pane, "echo hello", enter_delay=0.1)
    assert result.success is True
    assert result.attempts >= 1

    # Verify command was executed
    time.sleep(0.2)
    content = pane.capture_pane()
    assert any("hello" in line for line in content)


def test_send_safe_no_enter(pane):
    """Should send text without Enter when enter=False."""
    from twmux.lib.safe_input import send_safe

    time.sleep(0.3)

    # Clear and send text without enter
    pane.send_keys("C-c", enter=False)
    time.sleep(0.1)

    result = send_safe(pane, "test_no_enter", enter=False)
    assert result.success is True

    # Text should be in pane but not executed (no newline after)
    time.sleep(0.1)
    content = pane.capture_pane()
    # The text should appear on the command line
    assert any("test_no_enter" in line for line in content)

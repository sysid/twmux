"""Race-condition-safe tmux input operations."""

import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from libtmux import Pane


@dataclass
class SendResult:
    """Result of a safe send operation."""

    success: bool
    attempts: int


@dataclass
class WaitResult:
    """Result of wait_for_idle operation."""

    idle: bool
    elapsed: float


def _hash_content(content: list[str]) -> str:
    """Hash pane content for comparison."""
    text = "\n".join(content)
    return hashlib.md5(text.encode()).hexdigest()


def wait_for_idle(
    pane: "Pane",
    poll_interval: float = 0.2,
    stable_count: int = 3,
    timeout: float = 30.0,
) -> WaitResult:
    """Wait until pane output stabilizes.

    Args:
        pane: libtmux Pane object
        poll_interval: Seconds between content checks
        stable_count: Number of consecutive identical hashes required
        timeout: Maximum seconds to wait

    Returns:
        WaitResult with idle=True if stabilized, False if timeout
    """
    start = time.monotonic()
    last_hash = None
    consecutive = 0

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            return WaitResult(idle=False, elapsed=elapsed)

        content = pane.capture_pane()
        current_hash = _hash_content(content)

        if current_hash == last_hash:
            consecutive += 1
            if consecutive >= stable_count:
                return WaitResult(idle=True, elapsed=elapsed)
        else:
            consecutive = 1
            last_hash = current_hash

        time.sleep(poll_interval)


def send_safe(
    pane: "Pane",
    text: str,
    enter: bool = True,
    enter_delay: float = 0.05,
    max_retries: int = 3,
    retry_delay: float = 0.1,
) -> SendResult:
    """Send text to pane with optional Enter verification.

    Args:
        pane: libtmux Pane object
        text: Text to send
        enter: Whether to send Enter after text
        enter_delay: Delay between text and Enter (race condition mitigation)
        max_retries: Maximum Enter retry attempts
        retry_delay: Delay between retries

    Returns:
        SendResult indicating success and attempt count
    """
    # Send the text without Enter first
    pane.send_keys(text, enter=False)

    if not enter:
        return SendResult(success=True, attempts=1)

    # Delay before Enter to avoid race condition
    time.sleep(enter_delay)

    # Capture content before Enter for verification
    content_before = pane.capture_pane()

    for attempt in range(1, max_retries + 1):
        pane.enter()
        time.sleep(enter_delay)

        content_after = pane.capture_pane()

        # Verify Enter was received: content should change
        if content_after != content_before:
            return SendResult(success=True, attempts=attempt)

        # Retry with increasing delay
        time.sleep(retry_delay * attempt)

    return SendResult(success=False, attempts=max_retries)

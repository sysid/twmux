"""Shared pytest fixtures using libtmux."""

import pytest

# libtmux pytest plugin provides: server, session fixtures automatically
# We just need to add a pane fixture for convenience


@pytest.fixture
def pane(session):
    """Get the active pane from test session."""
    return session.active_window.active_pane

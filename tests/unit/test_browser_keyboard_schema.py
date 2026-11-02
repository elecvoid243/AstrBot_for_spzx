"""Exercise the closed browser keyboard vocabulary without an Agent Teams run."""

import pytest
from pydantic import ValidationError

from astrbot.dashboard.schemas import BrowserControlCommand


@pytest.mark.parametrize("payload", [
    {"operation": "evaluate", "script": "document.cookie"},
    {"operation": "text", "text": "x" * 2049, "source": "paste"},
    {"operation": "key", "modifiers": 16},
    {"operation": "key", "phase": "char"},
    {"operation": "text", "text": "hello", "source": "clipboard_poll"},
    {"operation": "key", "repeat": "true"},
])
def test_keyboard_schema_rejects_untrusted_messages(payload):
    """Reject oversized or open-ended public input before plugin dispatch.

    Args:
        payload: Untrusted WebSocket command body.
    """
    with pytest.raises(ValidationError):
        BrowserControlCommand.model_validate({"control_epoch": 1, **payload})

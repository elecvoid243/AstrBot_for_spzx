"""Check bounded public pointer commands independently of Agent Teams."""

import pytest
from pydantic import ValidationError

from astrbot.dashboard.schemas import BrowserControlCommand


@pytest.mark.parametrize("payload", [
    {"operation": "wheel", "delta_x": float("inf")},
    {"operation": "wheel", "delta_y": 2049},
    {"operation": "wheel", "delta_x": -2049},
    {"operation": "click", "click_count": 3},
    {"operation": "mouse", "script": "document.cookie"},
])
def test_pointer_schema_rejects_unbounded_input(payload):
    """Reject unbounded public messages before runtime dispatch.

    Args:
        payload: Untrusted pointer input body.
    """
    with pytest.raises(ValidationError):
        BrowserControlCommand.model_validate({"control_epoch": 1, **payload})

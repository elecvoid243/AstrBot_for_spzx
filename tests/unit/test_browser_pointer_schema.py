"""Check bounded public pointer commands independently of Agent Teams."""

import pytest
from pydantic import ValidationError

from astrbot.dashboard.schemas import BrowserControlCommand


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "wheel", "delta_x": float("inf")},
        {"operation": "wheel", "delta_y": 2049},
        {"operation": "wheel", "delta_x": -2049},
        {"operation": "click", "click_count": 3},
        {"operation": "pointer", "phase": "teleport"},
        {"operation": "pointer", "x": float("nan")},
        {"operation": "pointer", "x": -1},
        {"operation": "mouse", "script": "document.cookie"},
        {"operation": "resize", "width": 2561},
        {"operation": "resize", "height": 0},
        {"operation": "resize", "device_scale_factor": float("inf")},
    ],
)
def test_pointer_schema_rejects_unbounded_input(payload):
    """Reject unbounded public messages before runtime dispatch.

    Args:
        payload: Untrusted pointer input body.
    """
    with pytest.raises(ValidationError):
        BrowserControlCommand.model_validate({"control_epoch": 1, **payload})

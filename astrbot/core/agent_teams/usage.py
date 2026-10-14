"""Frozen admission limits and accounting over durable invocation receipts."""

from pydantic import BaseModel, ConfigDict, Field


class RunLimits(BaseModel):
    """Optional whole-scope ceilings; nested scopes also obey their ancestors."""

    model_config = ConfigDict(extra="forbid", strict=True)
    max_steps: int | None = Field(default=None, ge=1, le=100_000)
    max_members: int | None = Field(default=None, ge=1, le=10_000)
    max_iterations: int | None = Field(default=None, ge=1, le=10_000)
    token_budget: int | None = Field(default=None, ge=1, le=1_000_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)


class UsageStart(BaseModel):
    """One actual delivery, including a new delivery of an old invocation."""

    model_config = ConfigDict(extra="forbid", strict=True)
    receipt_id: str = Field(min_length=1, max_length=128)
    invocation_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(pattern="^(model|tool|query)$")
    name: str | None = Field(default=None, max_length=256)
    input_tokens: int | None = Field(default=None, ge=0)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)


class UsageReport(BaseModel):
    """Provider-reported tokens or a clearly marked best-effort estimate."""

    model_config = ConfigDict(extra="forbid", strict=True)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_tokens: int | None = Field(default=None, ge=0)


def summarize_usage(receipts: list[dict], limits: dict, details: bool) -> dict:
    """Aggregate each delivery once without hiding missing provider reports.

    Args:
        receipts: Unique stored deliveries in the selected task or subtree.
        limits: Frozen local scope limits.
        details: Include individual task, Attempt and Invocation references.

    Returns:
        Counters and separate reported, estimated, reserved and unknown usage.
    """
    return {
        "model_calls": sum(r["kind"] == "model" for r in receipts),
        "steps_used": len(receipts),
        "tool_calls": sum(r["kind"] != "model" for r in receipts),
        "limits": limits,
        "tokens": {
            "reported": sum(r.get("reported_tokens") or 0 for r in receipts)
            if any(r.get("reported_tokens") is not None for r in receipts)
            else None,
            "estimated": sum(
                r.get("estimated_tokens") or 0
                for r in receipts
                if r["quality"] != "in_flight"
            ),
            "reserved": sum(
                r["reserved_tokens"] for r in receipts if r["quality"] == "in_flight"
            ),
            "unknown_calls": sum(
                r["quality"] in {"unknown", "in_flight"} for r in receipts
            ),
        },
        "ledger": receipts if details else [],
    }

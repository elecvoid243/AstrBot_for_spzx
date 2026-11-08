"""Tests for the compaction budget resolver."""

from astrbot.core.agent.context.compaction_spec import resolve_compaction_spec


def test_threshold_subtracts_output_and_headroom():
    spec = resolve_compaction_spec(
        context_window=20_000,
        output_budget=2_000,
        threshold_ratio=0.8,
        headroom_tokens=2_000,
        retain_ratio=0.16,
        target_ratio=0.55,
    )
    assert spec is not None
    assert spec.threshold_tokens == 16_000
    assert spec.retain_tokens == 2_880
    assert spec.target_tokens == 11_000
    assert spec.max_summary_tokens == 2_000


def test_absolute_threshold_cannot_exceed_pressure_budget():
    spec = resolve_compaction_spec(
        context_window=20_000,
        output_budget=2_000,
        headroom_tokens=2_000,
        absolute_threshold_tokens=999_999,
    )
    assert spec is not None
    assert spec.threshold_tokens == 16_000


def test_absolute_threshold_can_lower_the_trigger():
    spec = resolve_compaction_spec(
        context_window=20_000,
        output_budget=2_000,
        headroom_tokens=2_000,
        absolute_threshold_tokens=10_000,
    )
    assert spec is not None
    assert spec.threshold_tokens == 10_000


def test_zero_message_budget_disables_compaction():
    assert resolve_compaction_spec(context_window=1_000, output_budget=1_000) is None


def test_oversized_headroom_falls_back_to_zero():
    spec = resolve_compaction_spec(
        context_window=1_000,
        output_budget=800,
        headroom_tokens=400,
    )
    assert spec is not None
    assert spec.headroom_tokens == 0
    assert spec.threshold_tokens == 200
    assert spec.target_tokens == 0


def test_retention_stays_below_threshold():
    spec = resolve_compaction_spec(
        context_window=20_000,
        output_budget=0,
        headroom_tokens=0,
        retain_ratio=0.99,
    )
    assert spec is not None
    assert spec.retain_tokens < spec.threshold_tokens


def test_target_disabled_when_not_below_threshold():
    spec = resolve_compaction_spec(
        context_window=1_000,
        output_budget=0,
        headroom_tokens=0,
        target_ratio=0.9,
    )
    assert spec is not None
    assert spec.target_tokens == 0


def test_invalid_values_fall_back_to_defaults():
    spec = resolve_compaction_spec(
        context_window="20000",  # type: ignore[arg-type]
        output_budget="bad",  # type: ignore[arg-type]
        threshold_ratio="bad",  # type: ignore[arg-type]
        headroom_tokens=-5,
        compaction_retries=99,
        min_growth_ratio="bad",  # type: ignore[arg-type]
    )
    assert spec is not None
    assert spec.context_window == 20_000
    assert spec.output_budget == 0
    assert spec.threshold_tokens == 16_000
    assert spec.compaction_retries == 3
    assert spec.min_growth_tokens == 2_000


def test_growth_never_exceeds_threshold():
    spec = resolve_compaction_spec(
        context_window=500,
        output_budget=0,
        headroom_tokens=0,
    )
    assert spec is not None
    assert spec.min_growth_tokens <= spec.threshold_tokens

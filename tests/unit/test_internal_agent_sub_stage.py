"""Tests for InternalAgentSubStage run-time binding overrides.

The full stage process() requires an extensive pipeline mock (session locks,
build_main_agent, run_agent, event results), so the pure overlay logic is
covered through the extracted _binding_overrides helper, which is what the
stage uses per event.
"""

from types import SimpleNamespace

from astrbot.core.astr_main_agent import MainAgentBuildConfig
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
    _binding_overrides,
)


def _make_event(binding) -> SimpleNamespace:
    """Build a minimal event stand-in exposing only get_extra."""
    return SimpleNamespace(
        get_extra=lambda key: binding if key == "agent_team_execution" else None
    )


def _make_cfg() -> MainAgentBuildConfig:
    return MainAgentBuildConfig(
        tool_call_timeout=120,
        fallback_max_context_tokens=128000,
    )


def test_binding_overrides_no_binding_keeps_defaults():
    cfg = _make_cfg()
    max_step, main_agent_cfg = _binding_overrides(_make_event(None), 30, cfg)
    assert max_step == 30
    assert main_agent_cfg is cfg


def test_binding_overrides_max_steps_only():
    cfg = _make_cfg()
    binding = SimpleNamespace(max_steps=7, tool_call_timeout=None, context_length=None)
    max_step, main_agent_cfg = _binding_overrides(_make_event(binding), 30, cfg)
    assert max_step == 7
    # Only max_steps set: the config object is returned untouched.
    assert main_agent_cfg is cfg


def test_binding_overrides_invalid_max_steps_keeps_default():
    """A crafted non-numeric max_steps must not raise inside process()."""
    cfg = _make_cfg()
    binding = SimpleNamespace(
        max_steps="abc", tool_call_timeout=None, context_length=None
    )
    max_step, main_agent_cfg = _binding_overrides(_make_event(binding), 30, cfg)
    assert max_step == 30
    assert main_agent_cfg is cfg


def test_binding_overrides_invalid_timeout_and_context_length_keep_defaults():
    """Crafted non-numeric timeout/context values fall back to snapshot defaults."""
    cfg = _make_cfg()
    binding = SimpleNamespace(
        max_steps=None, tool_call_timeout="soon", context_length="many"
    )
    _, main_agent_cfg = _binding_overrides(_make_event(binding), 30, cfg)
    assert main_agent_cfg is cfg
    assert main_agent_cfg.tool_call_timeout == 120
    assert main_agent_cfg.fallback_max_context_tokens == 128000


def test_binding_overrides_timeout_and_context_length():
    cfg = _make_cfg()
    binding = SimpleNamespace(
        max_steps=None, tool_call_timeout=90.5, context_length=8000
    )
    max_step, main_agent_cfg = _binding_overrides(_make_event(binding), 30, cfg)
    assert max_step == 30
    assert main_agent_cfg is not cfg
    assert main_agent_cfg.tool_call_timeout == 90.5
    assert main_agent_cfg.fallback_max_context_tokens == 8000
    # Concurrency-safety: the stage-level config object is never mutated.
    assert cfg.tool_call_timeout == 120
    assert cfg.fallback_max_context_tokens == 128000


def test_binding_overrides_timeout_only_keeps_context_length():
    cfg = _make_cfg()
    binding = SimpleNamespace(
        max_steps=None, tool_call_timeout=45.0, context_length=None
    )
    _, main_agent_cfg = _binding_overrides(_make_event(binding), 30, cfg)
    assert main_agent_cfg.tool_call_timeout == 45.0
    assert main_agent_cfg.fallback_max_context_tokens == 128000


def test_binding_overrides_context_length_only_keeps_timeout():
    cfg = _make_cfg()
    binding = SimpleNamespace(
        max_steps=None, tool_call_timeout=None, context_length=4000
    )
    _, main_agent_cfg = _binding_overrides(_make_event(binding), 30, cfg)
    assert main_agent_cfg.tool_call_timeout == 120
    assert main_agent_cfg.fallback_max_context_tokens == 4000

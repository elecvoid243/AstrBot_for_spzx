# Author: elecvoid243 · Created: 2026-10-25
"""One-shot skill-guide nudges queued from the dashboard.

The WebChat skill-guide menu queues a skill name for a session; the next
LLM request for that session drains the queue and appends a guidance text
part pointing the LLM at the skill's SKILL.md. One-shot semantics: the
entry is consumed by the first request, and neither the persona nor the
skill's global activation state is modified.

Migrated from the ``astrbot_plugin_skill_guide`` plugin (webapi handlers
now live in the dashboard skills API; injection happens natively in the
main-agent request assembly).
"""

from __future__ import annotations

import threading
from typing import Any


class GuideState:
    """Thread-safe per-umo queue of skill names waiting to be nudged."""

    def __init__(self) -> None:
        self._pending: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def queue(self, umo: str, skill_name: str) -> bool:
        """Queue ``skill_name`` for ``umo`` (deduplicated).

        Args:
            umo: Unified message origin of the session.
            skill_name: Skill name to nudge on the next request.

        Returns:
            True if newly queued, False if already pending.
        """
        with self._lock:
            pending = self._pending.setdefault(umo, [])
            if skill_name in pending:
                return False
            pending.append(skill_name)
            return True

    def drain(self, umo: str) -> list[str]:
        """Pop and clear all pending skills for ``umo`` (one-shot consumption).

        Args:
            umo: Unified message origin of the session.

        Returns:
            List of queued skill names (empty if none).
        """
        with self._lock:
            return self._pending.pop(umo, [])

    def clear(self, umo: str) -> list[str]:
        """Remove pending skills for ``umo`` without consuming them.

        Args:
            umo: Unified message origin of the session.

        Returns:
            List of removed skill names (empty if none).
        """
        with self._lock:
            return self._pending.pop(umo, [])


# Global singleton; entries are in-memory only and cleared on restart
# (acceptable under the one-shot semantics).
guide_state = GuideState()


def build_guidance(skill: Any) -> str:
    """Build the one-shot guidance text for a skill.

    Args:
        skill: Object exposing ``name``, ``description`` and ``path``
            attributes (duck-typed; ``SkillInfo`` works).

    Returns:
        English guidance paragraph instructing the LLM to use the skill.
    """
    name = str(getattr(skill, "name", "") or "").strip() or "<unnamed_skill>"
    description = str(getattr(skill, "description", "") or "").strip()
    if not description:
        description = "No description"
    path = str(getattr(skill, "path", "") or "").strip() or "<skill_path>"
    return (
        "[User skill request]\n"
        "The user explicitly asks you to use the following skill for this request:\n"
        f"- **{name}**: {description}\n"
        f"  File: `{path}`\n"
        "Read and follow its SKILL.md instructions before acting. Do not skip it."
    )


def validate_load_skill(known_skills: list[Any], skill_name: str) -> str | None:
    """Validate a skill-load request against the known skills.

    Args:
        known_skills: Every skill on disk (``resolve_all_skills``) — a
            manual load may target skills that are NOT session-active
            (not persona-mounted / deactivated), since the guidance
            prompt only needs the SKILL.md path.
        skill_name: Requested skill name.

    Returns:
        Error message string, or None when valid.
    """
    name = (skill_name or "").strip()
    if not name:
        return "missing skill_name"
    if not any(s.name == name for s in known_skills):
        return f"skill not found: {name}"
    return None


def build_active_payload(skills: list[Any], persona: Any) -> dict:
    """Build the payload for the guide-active endpoint.

    Args:
        skills: Resolved skill objects (``name``/``description``/``path``/
            ``source_type``).
        persona: Resolved persona dict or None.

    Returns:
        Payload with ``persona`` and serialized ``skills`` list.
    """
    return {
        "persona": (
            {"id": persona.get("persona_id"), "name": persona.get("name")}
            if persona
            else None
        ),
        "skills": [
            {
                "name": skill.name,
                "description": skill.description or "",
                "path": skill.path,
                "source_type": skill.source_type,
            }
            for skill in skills
        ],
    }


def _runtime_for(context: Any, umo: str) -> str:
    """Read the ``computer_use_runtime`` setting for a session."""
    cfg_obj = context.get_config(umo=umo)
    prov_settings = cfg_obj.get("provider_settings", {}) or {}
    return prov_settings.get("computer_use_runtime", "local")


async def resolve_all_skills(context: Any, umo: str) -> list[Any]:
    """List every skill on disk for the session's runtime (no filters).

    Manual loads may target skills that are NOT effective for the session
    (not persona-mounted, globally deactivated, or from a disabled plugin):
    a skill is just files, and the guidance prompt only points the agent at
    the SKILL.md path.

    Args:
        context: The star Context (config access).
        umo: Unified message origin of the session.

    Returns:
        The full skill list (``SkillManager().list_skills(active_only=False)``).
    """
    from astrbot.core.skills.skill_manager import SkillManager

    return SkillManager().list_skills(
        active_only=False,
        runtime=_runtime_for(context, umo),
    )


async def resolve_active_skills(context: Any, umo: str) -> tuple[list[Any], Any | None]:
    """Resolve the skills effective for ``umo`` (mirrors the main agent).

    Steps: provider settings from ``context.get_config(umo)`` ->
    ``SkillManager().list_skills(active_only=True)`` ->
    plugin-availability filter -> conversation persona ->
    ``filter_by_persona``.

    Args:
        context: The star Context (config, conversation and persona access).
        umo: Unified message origin of the session.

    Returns:
        Tuple of ``(skills, persona)``; ``persona`` is None when the
        session resolves to no persona (webchat default included).
    """
    from astrbot.core.skills.skill_manager import SkillManager
    from astrbot.core.star.star import star_registry

    cfg_obj = context.get_config(umo=umo)
    prov_settings = cfg_obj.get("provider_settings", {}) or {}
    runtime = prov_settings.get("computer_use_runtime", "local")

    skills = _filter_plugin_skills(
        SkillManager().list_skills(active_only=True, runtime=runtime),
        prov_settings.get("plugin_set", ["*"]),
        star_registry,
    )

    conversation_persona_id: str | None = None
    try:
        conv_mgr = context.conversation_manager
        cid = await conv_mgr.get_curr_conversation_id(umo)
        if cid:
            conv = await conv_mgr.get_conversation(umo, cid)
            conversation_persona_id = getattr(conv, "persona_id", None) or None
    except Exception:  # persona lookup must never break the API
        conversation_persona_id = None

    platform_name = (umo.split(":", 1)[0] if umo else "") or "webchat"
    _, persona, _, _ = await context.persona_manager.resolve_selected_persona(
        umo=umo,
        conversation_persona_id=conversation_persona_id,
        platform_name=platform_name,
        provider_settings=prov_settings,
    )
    persona_skills = persona.get("skills") if persona else None
    if persona_skills is None:
        pass
    elif not persona_skills:
        skills = []
    else:
        allowed = set(persona_skills)
        skills = [skill for skill in skills if skill.name in allowed]
    return skills, persona


def _filter_plugin_skills(
    skills: list[Any],
    plugin_set: Any,
    star_registry: Any,
) -> list[Any]:
    """Drop plugin-provided skills whose plugin is disabled or filtered.

    Mirrors ``astr_main_agent._filter_skills_for_current_config``.

    Args:
        skills: Skill objects with ``source_type`` and ``plugin_name``.
        plugin_set: ``["*"]``/missing = allow all; else allowed plugin names.
        star_registry: Iterable of plugin metadata objects.

    Returns:
        Filtered skill list.
    """
    allowed_plugins: set[str] | None = None
    if isinstance(plugin_set, list) and "*" not in plugin_set:
        allowed_plugins = {str(name) for name in plugin_set}

    plugin_by_root_dir = {
        meta.root_dir_name: meta
        for meta in star_registry
        if getattr(meta, "root_dir_name", None)
    }

    filtered: list[Any] = []
    for skill in skills:
        if getattr(skill, "source_type", None) != "plugin":
            filtered.append(skill)
            continue
        plugin = plugin_by_root_dir.get(getattr(skill, "plugin_name", None))
        if not plugin or not plugin.activated:
            continue
        if plugin.reserved or allowed_plugins is None:
            filtered.append(skill)
            continue
        if plugin.name is not None and plugin.name in allowed_plugins:
            filtered.append(skill)
    return filtered


def inject_pending_guidance(umo: str, req: Any, runtime: str) -> list[str]:
    """Drain the pending queue for ``umo`` and append guidance parts to ``req``.

    Called natively from the main-agent request assembly. Pending names that
    cannot be resolved against the full on-disk skill list (e.g. the skill
    was removed meanwhile) are silently dropped; the queue is drained
    regardless (one-shot semantics).

    The guidance MUST land in the saved conversation history: inference
    engines reuse the prefix cache across turns, and dropping this part on
    the next history rebuild would invalidate the cached prefix of the
    previous reply. Hence a persisted ``TextPart``, not a temp part.

    Args:
        umo: Unified message origin of the session.
        req: ProviderRequest-like object exposing
            ``extra_user_content_parts`` list.
        runtime: ``computer_use_runtime`` setting (skill path resolution).

    Returns:
        The list of skill names actually injected (empty when none).
    """
    from astrbot.core.agent.message import TextPart
    from astrbot.core.skills.skill_manager import SkillManager

    pending = guide_state.drain(umo)
    if not pending:
        return []
    by_name = {
        skill.name: skill
        for skill in SkillManager().list_skills(active_only=False, runtime=runtime)
    }
    parts: list[TextPart] = []
    injected: list[str] = []
    for name in pending:
        skill = by_name.get(name)
        if skill is None:
            continue
        injected.append(name)
        parts.append(TextPart(text=build_guidance(skill)))
    if parts:
        req.extra_user_content_parts.extend(parts)
    return injected

# Agent Teams Member Config Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 4th "成员" tab to the Agent Teams panel where each team member's execution config (config profile, persona+Tools+Skills, provider, max tool rounds, tool timeout, knowledge base, context length) can be viewed and edited, persisted on the member row, and effective at run time.

**Architecture:** Members gain an optional `runner_config` dict block on the `AgentTeam.members` JSON list. A new `update_member` service method + `PATCH /agent_teams/{team_id}/members/{member_id}` endpoint validate and persist it. At run time `agent_team_run_service._execute_node` merges the member's config under the node's `execution` block (node wins) into an extended `NodeExecutionBinding`; existing seams (config_id→scheduler, persona/tools/skills→astr_main_agent, provider→`selected_provider` extra, max_steps/tool_call_timeout/context→InternalAgentSubStage per-event overlay, kb_names→member session `kb_config`) turn each field into a per-member effective override.

**Tech Stack:** Python 3.10+ / AstrBot core (dataclasses, FastAPI routes, Pydantic-ish validation), Vue 3.3 + Vuetify 3 + Pinia dashboard, vitest + @vue/test-utils.

**Spec:** `docs/superpowers/specs/2026-09-06-agent-teams-member-config-design.md`

## Global Constraints

- English comments and logs; Google-format docstrings (`Args:`, `Returns:`, `Raises:`) on public functions.
- KISS: no new dependencies, no schema migrations (member config is JSON on an existing column), no helper functions unless reused 3+ times.
- i18n parity: every new UI string added to zh-CN, en-US, ru-RU `dashboard/src/i18n/locales/*/features/agent-teams.json`.
- Vue 3.3: no `defineModel`; use `modelValue`/`update:modelValue`. English code comments.
- Run `ruff format .` and `ruff check .` on backend files after edits; `npx vue-tsc --noEmit` must stay clean.
- Existing tests must stay green: agent_teams frontend 154 + backend suite.

---

### Task 1: Backend — member `runner_config` + `update_member` service & API

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_service.py` (`update_team` ~233-253, `add_member` ~272-283, `_validate_node_execution` ~416-479)
- Modify: `astrbot/dashboard/api/agent_teams.py` (routes ~124-151)
- Test: `tests/dashboard/test_agent_team_service.py` (update_member tests)

**Interfaces:**
- Consumes: existing `AgentTeamsServiceError`, `_validate_node_execution`, `_create_member` pin helpers (`conversation_manager.new_conversation`, `provider_manager.set_provider`), `update_agent_team`.
- Produces: `update_member(username: str, team_id: str, member_id: str, payload: dict) -> dict` (validated member dict); `PATCH /api/v1/agent_teams/{team_id}/members/{member_id}` route returning the updated team.

- [ ] **Step 1: Write the failing tests**

```python
# in tests/dashboard/test_agent_team_service.py
import pytest

@pytest.mark.asyncio
async def test_update_member_persists_runner_config(service, team_factory):
    team = await team_factory(members=[{"name": "Alice", "system_prompt": "p"}])
    member_id = team["members"][0]["member_id"]
    updated = await service.update_member(
        "owner0",
        team["team_id"],
        member_id,
        {"name": "Alice", "runner_config": {"config_id": "conf0", "max_steps": 12}},
    )
    member = next(m for m in updated["members"] if m["member_id"] == member_id)
    assert member["runner_config"]["config_id"] == "conf0"
    assert member["runner_config"]["max_steps"] == 12
    # legacy fields untouched
    assert member["session_id"]

@pytest.mark.asyncio
async def test_update_member_rejects_unknown_member(service, team_factory):
    team = await team_factory(members=[{"name": "Alice", "system_prompt": "p"}])
    with pytest.raises(Exception):
        await service.update_member("owner0", team["team_id"], "nope", {"name": "X"})

@pytest.mark.asyncio
async def test_update_member_rejects_invalid_config_id(service, team_factory, monkeypatch):
    team = await team_factory(members=[{"name": "Alice", "system_prompt": "p"}])
    member_id = team["members"][0]["member_id"]
    monkeypatch.setattr(service.core_lifecycle.astrbot_config_mgr, "confs", {})
    with pytest.raises(Exception):
        await service.update_member("owner0", team["team_id"], member_id,
                                    {"runner_config": {"config_id": "missing"}})

@pytest.mark.asyncio
async def test_update_member_rejects_out_of_range_numbers(service, team_factory):
    team = await team_factory(members=[{"name": "Alice", "system_prompt": "p"}])
    member_id = team["members"][0]["member_id"]
    with pytest.raises(Exception):
        await service.update_member("owner0", team["team_id"], member_id,
                                    {"runner_config": {"max_steps": 0}})
    with pytest.raises(Exception):
        await service.update_member("owner0", team["team_id"], member_id,
                                    {"runner_config": {"tool_call_timeout": 99999}})
    with pytest.raises(Exception):
        await service.update_member("owner0", team["team_id"], member_id,
                                    {"runner_config": {"context_length": -5}})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd F:/github/Astrbot && uv run pytest tests/dashboard/test_agent_team_service.py -x -k update_member -v`
Expected: FAIL — `update_member` does not exist.

- [ ] **Step 3: Implement `update_member` + validation**

In `agent_team_service.py`, add constants and the validator (mirror `_validate_node_execution`'s style; reuse `_validate_node_execution` for config_id/tools/skills via a shared helper — extract its block if it already enforces them, otherwise re-implement the same rules):

```python
MAX_MEMBER_MAX_STEPS = 200
MAX_MEMBER_TOOL_TIMEOUT = 3600
MAX_MEMBER_CONTEXT_LENGTH = 1_000_000

def _validate_member_runner_config(self, runner_config: dict) -> dict:
    """Validate a member's runner_config block.

    Args:
        runner_config: The raw runner_config dict from the payload.

    Returns:
        The normalized dict with only known keys (empty values dropped).

    Raises:
        AgentTeamsServiceError: When a referenced profile/persona is unknown
            or a numeric field is out of range.
    """
    if not isinstance(runner_config, dict):
        raise AgentTeamsServiceError("runner_config 必须是对象")
    out: dict = {}
    if "config_id" in runner_config and runner_config["config_id"] not in (None, ""):
        config_id = str(runner_config["config_id"])
        if config_id not in self.core_lifecycle.astrbot_config_mgr.confs:
            raise AgentTeamsServiceError(f"配置档案不存在: {config_id}")
        out["config_id"] = config_id
    for key in ("tools", "skills", "kb_names"):
        value = runner_config.get(key)
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise AgentTeamsServiceError(f"{key} 必须是字符串列表")
        out[key] = value
    if "max_steps" in runner_config and runner_config["max_steps"] is not None:
        v = int(runner_config["max_steps"])
        if not 1 <= v <= MAX_MEMBER_MAX_STEPS:
            raise AgentTeamsServiceError("max_steps 超出范围")
        out["max_steps"] = v
    if "tool_call_timeout" in runner_config and runner_config["tool_call_timeout"] is not None:
        v = float(runner_config["tool_call_timeout"])
        if not 0 < v <= MAX_MEMBER_TOOL_TIMEOUT:
            raise AgentTeamsServiceError("tool_call_timeout 超出范围")
        out["tool_call_timeout"] = v
    if "context_length" in runner_config and runner_config["context_length"] is not None:
        v = int(runner_config["context_length"])
        if not 1 <= v <= MAX_MEMBER_CONTEXT_LENGTH:
            raise AgentTeamsServiceError("context_length 超出范围")
        out["context_length"] = v
    return out


async def update_member(self, username: str, team_id: str, member_id: str, payload: dict) -> dict:
    """Update an existing team member (name, persona/provider pin, runner_config).

    Args:
        username: Owner of the team.
        team_id: The team id.
        member_id: The member id to update.
        payload: Fields to update: name, persona_id, provider_id,
            system_prompt, runner_config.

    Returns:
        The updated team dict.

    Raises:
        AgentTeamsServiceError: When the team/member is missing, the name is
            duplicated, or the payload fails validation.
    """
    team = await self.get_team(team_id, username)
    member = next((m for m in team.get("members", []) if m.get("member_id") == member_id), None)
    if not member:
        raise AgentTeamsServiceError("成员不存在")
    updated = dict(member)
    if "name" in payload:
        name = str(payload["name"]).strip()
        if not name or len(name) > MAX_NAME_LEN:
            raise AgentTeamsServiceError("成员名称无效")
        if any(m.get("name") == name and m["member_id"] != member_id for m in team["members"]):
            raise AgentTeamsServiceError("成员名称已存在")
        updated["name"] = name
    if "persona_id" in payload:
        persona_id = payload["persona_id"] or None
        if persona_id:
            try:
                await self.core_lifecycle.persona_manager.get_persona_v3_by_id(persona_id)
            except Exception as exc:  # noqa: BLE001
                raise AgentTeamsServiceError(f"人格不存在: {persona_id}") from exc
        updated["persona_id"] = persona_id
    if "provider_id" in payload:
        updated["provider_id"] = payload["provider_id"] or None
    if "system_prompt" in payload:
        updated["system_prompt"] = payload["system_prompt"] or None
    if "runner_config" in payload:
        updated["runner_config"] = self._validate_member_runner_config(payload["runner_config"])

    # Re-pin the member session (same helpers as _create_member) so the new
    # persona/provider take effect on the next dispatch.
    umo = member.get("umo") or ""
    if updated.get("persona_id"):
        await self.core_lifecycle.conversation_manager.new_conversation(
            umo, "webchat", persona_id=updated["persona_id"]
        )
    if updated.get("provider_id"):
        await self.core_lifecycle.provider_manager.set_provider(
            updated["provider_id"], ProviderType.CHAT_COMPLETION, umo
        )

    members = [
        updated if m["member_id"] == member_id else m for m in team.get("members", [])
    ]
    return await self.update_agent_team(team_id, members=members)
```

Then adapt the route in `agent_teams.py`:

```python
@router.patch("/agent_teams/{team_id}/members/{member_id}")
async def update_member(team_id: str, member_id: str, payload: dict, request: Request):
    """Update a member of an agent team."""
    service = request.app.state.agent_team_service
    username = get_username(request)
    try:
        team = await service.update_member(username, team_id, member_id, payload)
    except AgentTeamsServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "ok", "message": "", "data": team}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd F:/github/Astrbot && uv run pytest tests/dashboard/test_agent_team_service.py -x -k update_member -v`
Expected: PASS. Fix any expectations around `update_agent_team` signature by reading it first.

- [ ] **Step 5: Commit**

```bash
git add astrbot/dashboard/services/agent_team_service.py astrbot/dashboard/api/agent_teams.py tests/dashboard/test_agent_team_service.py
git commit -m "feat(agent-teams): add update_member service and API"
```

---

### Task 2: Backend — merge member config into the execution binding

**Files:**
- Modify: `astrbot/core/agent_team_execution.py` (`NodeExecutionBinding` dataclass 23-46)
- Modify: `astrbot/dashboard/services/agent_team_run_service.py` (`_execute_node` ~386-521)
- Test: `tests/dashboard/test_agent_team_run_service.py` or existing run test file

**Interfaces:**
- Consumes: `NodeExecutionBinding(run_id, team_id, member_id, node_id, umo, owner_username, config_id, persona_id, tools, skills)`; member dict rows.
- Produces: binding with additional fields `provider_id`, `max_steps`, `tool_call_timeout`, `kb_names`, `context_length` (KEEP ALL NULLABLE with `= None` defaults so existing constructions stay valid).

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_execute_node_merges_member_runner_config_under_node(run_service, team_factory):
    """Node execution block wins; member runner_config fills the gaps."""
    team = await team_factory(members=[{
        "name": "Alice", "persona_id": "persona_a", "session_id": "s1", "umo": "webchat:s1",
        "runner_config": {"config_id": "conf0", "max_steps": 12, "provider_id": "prov_a"},
    }])
    from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
    reg_spy = ...  # spy is not needed: assert on the registered binding via a token hook
    # Run a node with execution={"persona_id": "persona_b", "tools": ["t1"]} and
    # assert the resolved binding has config_id='conf0' (member), persona_id='persona_b'
    # (node wins), provider_id='prov_a' (member), max_steps=12 (member).
```

If the existing test seams make this awkward, the implementer may assert on the dataclass directly by unit-testing the merge helper instead:

```python
def test_merge_member_execution_precedence():
    from astrbot.dashboard.services.agent_team_run_service import _merged_member_execution
    merged = _merged_member_execution(
        {"persona_id": "persona_a", "provider_id": "prov_a",
         "runner_config": {"max_steps": 12, "tools": ["t1"]}},
        node_execution={"persona_id": "persona_b", "tools": ["t2"]},
    )
    assert merged == {
        "persona_id": "persona_b",  # node wins
        "provider_id": "prov_a",    # member top-level
        "max_steps": 12,            # member runner_config
        "tools": ["t2"],            # node wins
    }
```

Implement the helper: `def _merged_member_execution(member: dict, node_execution: dict | None) -> dict` at module level (public for testing): `member_level = {**member.get("runner_config", {}), "persona_id": member.get("persona_id") or None, "provider_id": member.get("provider_id") or None}`; return `{**member_level, **(node_execution or {})}`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/dashboard/test_agent_team_run_service.py -k merged_member_execution -v`
Expected: FAIL — helper not defined.

- [ ] **Step 3: Implement**

`agent_team_execution.py` — extend the dataclass (new fields with `= None` defaults after `skills`):

```python
    provider_id: str | None = None
    """Provider override for this member turn, if any."""
    max_steps: int | None = None
    """Tool-loop step limit override (agent_runner.config.misc.max_steps)."""
    tool_call_timeout: float | None = None
    """Tool call timeout override in seconds."""
    kb_names: list[str] | None = None
    """Knowledge base names override (kb_names)."""
    context_length: int | None = None
    """Context length override (request-level model/compression cap)."""
```

`agent_team_run_service.py::_execute_node` — replace the raw `execution` extraction (around line 402) with the merge and pass extra kwargs when constructing `NodeExecutionBinding` (around 498-511):

```python
    execution = _merged_member_execution(member, node.get("execution") or {}) or {}
    # existing config checker for execution.get("config_id") unchanged
    ...
    binding = NodeExecutionBinding(
        run_id=run_id, team_id=team_id, member_id=member.get("member_id"),
        node_id=node_id, umo=umo, owner_username=owner_username,
        config_id=execution.get("config_id") or None,
        persona_id=execution.get("persona_id") or None,
        tools=execution.get("tools"),
        skills=execution.get("skills"),
        provider_id=execution.get("provider_id") or None,
        max_steps=execution.get("max_steps"),
        tool_call_timeout=execution.get("tool_call_timeout"),
        kb_names=execution.get("kb_names"),
        context_length=execution.get("context_length"),
    )
```

(Read `_execute_node` first; adapt names of local variables to its actual code.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/dashboard/test_agent_team_run_service.py -k "merged_member or execute_node" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/agent_team_execution.py astrbot/dashboard/services/agent_team_run_service.py tests/dashboard/test_agent_team_run_service.py
git commit -m "feat(agent-teams): merge member runner_config into execution binding"
```

---

### Task 3: Backend — apply binding overrides at run time

**Files:**
- Modify: `astrbot/core/astr_main_agent.py` (`_select_provider` ~250-289)
- Modify: `astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py` (initialize snapshot at 53-142; process() at 185+; max_step usages at ~342/373/403; main_agent_cfg usage at ~249)
- Modify: `astrbot/dashboard/services/agent_team_service.py` (`update_member` — kb_names session wiring)
- Test: add focused unit tests where seams allow (provider via event extra; max_step override via a process-level helper).

**Interfaces:**
- Consumes: `event.get_extra("agent_team_execution")` returning `NodeExecutionBinding | None`.
- Produces: effective per-request overrides for provider_id, max_steps, tool_call_timeout, context_length, kb_names.

- [ ] **Step 1: Provider override — implement + test**

In `_select_provider`, after the `selected_provider` block, before the default `get_using_provider_async`:

```python
    binding = event.get_extra("agent_team_execution")
    binding_provider = getattr(binding, "provider_id", None)
    if binding_provider:
        provider = plugin_context.get_provider_by_id(binding_provider)
        if provider is None or not isinstance(provider, Provider):
            logger.error("Agent team binding provider %r not found.", binding_provider)
            _set_llm_error_message(event, f"LLM 请求失败：未找到指定的提供商 `{binding_provider}`。")
            return None
        return provider
```

Test stub (no real LLM): call `_select_provider` with an event whose extra `agent_team_execution` is a `simple_namespace(provider_id="prov_a", ...)` and a plugin_context whose `get_provider_by_id` is monkeypatched; assert it returns the provider and does NOT call `get_using_provider_async`.

- [ ] **Step 2: max_steps / tool_call_timeout / context_length overlay in the stage**

In `agent_sub_stages/internal.py::process`, once per event at the top (after the `has_valid_message` guards):

```python
        binding = event.get_extra("agent_team_execution")
        max_step = self.max_step
        main_agent_cfg = self.main_agent_cfg
        if binding is not None:
            if getattr(binding, "max_steps", None) is not None:
                max_step = int(binding.max_steps)
            if (
                getattr(binding, "tool_call_timeout", None) is not None
                or getattr(binding, "context_length", None) is not None
            ):
                import dataclasses

                main_agent_cfg = dataclasses.replace(
                    self.main_agent_cfg,
                    tool_call_timeout=(
                        float(binding.tool_call_timeout)
                        if getattr(binding, "tool_call_timeout", None) is not None
                        else self.main_agent_cfg.tool_call_timeout
                    ),
                    fallback_max_context_tokens=(
                        int(binding.context_length)
                        if getattr(binding, "context_length", None) is not None
                        else self.main_agent_cfg.fallback_max_context_tokens
                    ),
                )
```

Then replace `self.max_step` occurrences inside `process()`/`process`-called methods with `max_step` (site checks: lines ~342/373/403) and `self.main_agent_cfg` at ~249 with `main_agent_cfg`. Follow the data flow: wherever `max_step`/`main_agent_cfg` are passed to calls like `step_until_done(max_steps=...)` or `_decorate_llm_request(..., main_agent_cfg)`, pass the local override. Keep `initialize()` untouched.

- [ ] **Step 3: kb_names wiring in update_member**

In `update_member`, when `runner_config` contains `kb_names`:

```python
            kb_names = updated["runner_config"].get("kb_names")
            if kb_names is not None:
                await self._apply_member_kb_config(umo, kb_names)
```

Add the helper (async; map names→ids using the kb manager, then set the member session's kb_config):

```python
    async def _apply_member_kb_config(self, umo: str, kb_names: list[str]) -> None:
        """Pin the member session's knowledge base config.

        Args:
            umo: The member's unified message origin.
            kb_names: Knowledge base names to enable (empty disables).
        """
        from astrbot.core import sp

        if not kb_names:
            await sp.session_set(umo, "kb_config", {})
            return
        kb_mgr = self.core_lifecycle.kb_manager
        kb_ids = []
        for name in kb_names:
            helper = await kb_mgr.get_kb_by_name(name)
            if helper and helper.kb:
                kb_ids.append(helper.kb.kb_id)
        if kb_ids:
            await sp.session_set(umo, "kb_config", {"kb_ids": kb_ids, "top_k": 5})
```

(Verify the kb manager attribute name (`kb_manager`) and `kb.kb_id` field against `astrbot/core/knowledge_base/kb_mgr.py`; adjust if different. `retrieve_knowledge_base` in `tools/knowledge_base_tools.py` already prefers the session config when `"kb_ids"` is present, so no runner change is needed.)

- [ ] **Step 4: Run existing tests**

Run: `uv run pytest tests/ -k agent_team -q` and `uv run pytest tests/dashboard/test_agent_team_run_service.py -q`
Expected: PASS (no regressions). Add the provider-override unit test described in Step 1 to the committed test file.

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/astr_main_agent.py astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py astrbot/dashboard/services/agent_team_service.py
git commit -m "feat(agent-teams): apply member binding overrides at run time"
```

---

### Task 4: Frontend — API facade + i18n keys

**Files:**
- Modify: `dashboard/src/api/v1.ts` (`agentTeamsApi` ~1987-2010)
- Modify: `dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU}/features/agent-teams.json`

**Interfaces:**
- Produces: `agentTeamsApi.updateMember(teamId, memberId, payload) => Promise<AxiosResponse>`; `MemberConfigPanel` consumes it.

- [ ] **Step 1: Add the facade**

```ts
  async updateMember(
    teamId: string,
    memberId: string,
    payload: Record<string, unknown>,
  ) {
    const url = `${baseUrl}/agent_teams/${teamId}/members/${memberId}`;
    const data = await request({
      url,
      method: 'patch',
      data: payload,
    });
    return unwrapEnvelope(data);
  },
}
```

(Copy the `request`/`unwrapEnvelope`/`baseUrl` conventions of the existing `addMember` in the same object.)

- [ ] **Step 2: Add i18n keys (parity across 3 locales)**

Add a `memberConfig` object to each `agent-teams.json`:

```json
"memberConfig": {
  "title": "成员配置",
  "listTitle": "成员",
  "selectHint": "点击左侧成员查看并修改配置",
  "name": "成员名称",
  "configProfile": "配置档案",
  "configProfileDefault": "跟随会话默认",
  "persona": "人格",
  "personaHint": "选择人格后可编辑其工具与 Skill",
  "tools": "工具",
  "toolsInherit": "跟随人格",
  "toolsDisableAll": "全部禁用",
  "skills": "Skill",
  "skillsInherit": "跟随人格",
  "skillsDisableAll": "全部禁用",
  "provider": "Provider",
  "providerDefault": "使用会话默认模型",
  "maxSteps": "工具调用最大轮数",
  "toolCallTimeout": "工具超时（秒）",
  "contextLength": "上下文长度（token）",
  "knowledgeBase": "知识库",
  "kbNone": "不使用知识库",
  "save": "保存配置",
  "saveSuccess": "成员配置已保存",
  "saveFailed": "保存失败",
  "loadFailed": "加载配置失败"
}
```

Translate to en-US and ru-RU with matching key sets (the existing `errors.loadFailed`/`saveFailed` style).

- [ ] **Step 3: Verify JSON validity**

Run: `cd F:/github/Astrbot/dashboard && node -e "['zh-CN','en-US','ru-RU'].forEach(l=>{const j=require('./src/i18n/locales/'+l+'/features/agent-teams.json'); if(!j.memberConfig||Object.keys(j.memberConfig).length<10) throw new Error(l)})"`

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/api/v1.ts dashboard/src/i18n/locales/zh-CN/features/agent-teams.json dashboard/src/i18n/locales/en-US/features/agent-teams.json dashboard/src/i18n/locales/ru-RU/features/agent-teams.json
git commit -m "feat(dashboard): add updateMember API and member config i18n"
```

---

### Task 5: Frontend — MemberConfigPanel + MemberConfigForm components

**Files:**
- Create: `dashboard/src/components/agent_teams/MemberConfigPanel.vue`
- Create: `dashboard/src/components/agent_teams/MemberConfigForm.vue`
- Create: `dashboard/src/components/agent_teams/MemberConfigPanel.spec.ts`
- Test: existing stub patterns from `TeamCreateDialog.spec.ts`/`MemberAddDialog.spec.ts`

**Interfaces:**
- Consumes: props `team` (`any`); `agentTeamsApi.updateMember`; `configProfileApi.list`; `personaApi.tree/list` (via `PersonaSelector`); `toolApi.list`; `skillApi.list`; `providerApi.listByProviderType('chat_completion')`; `useToast`; `useModuleI18n('features/agent-teams')`.
- Produces: `MemberConfigPanel` emits `updateTeam` (`team: any`) so the page can refresh its team state after save.

- [ ] **Step 1: Write the component spec (failing first)**

```ts
import { mount, flushPromises } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import zh from '@/i18n/locales/zh-CN/features/agent-teams.json';
import MemberConfigPanel from './MemberConfigPanel.vue';

const apiMocks = vi.hoisted(() => ({
  updateMember: vi.fn(),
  configProfileList: vi.fn(),
  toolList: vi.fn(),
  skillList: vi.fn(),
  providerList: vi.fn(),
}));

vi.mock('@/api/v1', () => ({
  agentTeamsApi: { updateMember: apiMocks.updateMember },
  configProfileApi: { list: apiMocks.configProfileList },
  personaApi: {},
  toolApi: { list: apiMocks.toolList },
  skillApi: { list: apiMocks.skillList },
  providerApi: { listByProviderType: apiMocks.providerList },
}));
vi.mock('@/utils/toast', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));

const TEAM = {
  team_id: 't1',
  name: 'Alpha',
  coordinator_member_id: 'm1',
  members: [
    { member_id: 'm1', name: 'Alice', persona_id: 'persona_a' },
    { member_id: 'm2', name: 'Bob' },
  ],
};

describe('MemberConfigPanel', () => {
  it('renders the member list and selects the first member', async () => {
    const wrapper = mount(MemberConfigPanel, { props: { team: TEAM }, global: { stubs: { 'v-select': true, 'v-text-field': true, 'v-textarea': true, 'v-btn': true, 'v-chip': true } } });
    expect(wrapper.findAll('.member-config-item')).toHaveLength(2);
  });

  it('saves the form payload via updateMember', async () => {
    apiMocks.updateMember.mockResolvedValue({ data: { status: 'ok', data: TEAM } });
    const wrapper = mount(MemberConfigPanel, {
      props: { team: TEAM },
      global: { stubs: { /* as above plus PersonaSelector/KnowledgeBaseSelector */ } },
    });
    await wrapper.findAll('.member-config-item')[0]!.trigger('click');
    await wrapper.find('[data-test="member-config-save"]').trigger('click');
    await flushPromises();
    expect(apiMocks.updateMember).toHaveBeenCalledWith(
      't1', 'm1', expect.objectContaining({ name: 'Alice' }),
    );
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd F:/github/Astrbot/dashboard && npx vitest run src/components/agent_teams/MemberConfigPanel.spec.ts`
Expected: FAIL — component does not exist.

- [ ] **Step 3: Implement the components**

`MemberConfigPanel.vue` (script setup): receives `team`; keeps `selectedMemberId` (default first member); renders a left member list (`member-config-item` class + `collabMemberColor` dot + coordinator badge) and the right `MemberConfigForm` bound to the selected member; on `@saved` re-emits `updateTeam` with the returned team.

`MemberConfigForm.vue`:
- Props: `member` (row), `team`.
- Local draft refs: `name`, `configId`, `personaId`, `toolsMode` ('inherit' | 'disable_all'), `tools: string[]`, `skillsMode`, `skills: string[]`, `providerId`, `maxSteps`, `toolCallTimeout`, `contextLength`, `kbNames: string[]`.
- Load options lazily on mount: `configProfileApi.list()` (items `{title,name,value,id}` — see WorkflowEditor's mapping), `toolApi.list()` (filter `active !== false`), `skillApi.list()`, `providerApi.listByProviderType('chat_completion')`.
- Persona UI: reuse `PersonaSelector` (`v-model="personaId"`). Tools/Skills checkboxes appear after a persona is chosen: each renders a `v-checkbox-btn` per tool/skill with `inherit` (default) / `disable_all` radio (pattern from `WorkflowEditor.vue` exec group; serialize: `tools: inherit ? [] (omit) : disable_all ? [] : tools` — match node execution semantics: `[]` = disable all, list = allowlist, omit/None = follow).
- Numeric fields use `<v-text-field type="number">` bound to `Number(...)` with min/max hints from `editor` validation ranges.
- KB: `v-select multiple` over `knowledgeBaseApi`-equivalent options — use the same options source the dashboard KB selector uses (`KnowledgeBaseSelector` component props; if it is a folder-tree picker like PersonaSelector, mount it with the same `v-model` contract and read its emitted value; otherwise use `v-select` fed by a kb list API — check `dashboard/src/api/v1.ts` for a kb listing facade and reuse it).
- Save button `[data-test="member-config-save"]`: builds payload `{ name, persona_id, provider_id, system_prompt: member.system_prompt, runner_config: { config_id, tools (per mode), skills (per mode), max_steps, tool_call_timeout, context_length, kb_names } }` — omit empty fields; calls `agentTeamsApi.updateMember(team.team_id, member.member_id, payload)`; seeds values on save-success toast `tm('memberConfig.saveSuccess')`; on error `extractApiError` + `toast.error`.
- On `member.member_id` change: reset the draft from the new row.

- [ ] **Step 4: Run tests**

Run: `cd F:/github/Astrbot/dashboard && npx vitest run src/components/agent_teams/MemberConfigPanel.spec.ts src/components/agent_teams/WorkflowEditor.spec.ts`
Expected: PASS (both files).

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/agent_teams/MemberConfigPanel.vue dashboard/src/components/agent_teams/MemberConfigForm.vue dashboard/src/components/agent_teams/MemberConfigPanel.spec.ts
git commit -m "feat(dashboard): add member config panel and form"
```

---

### Task 6: Frontend — Members tab on AgentTeamsPage

**Files:**
- Modify: `dashboard/src/views/AgentTeamsPage.vue` (tabs ~ the `tabs` model with editor/monitor/history; tab bar template)
- Modify: `dashboard/src/views/AgentTeamsPage.spec.ts` (if present; else add tab assertions to an existing related spec)
- Modify: `dashboard/src/i18n/locales/*/features/agent-teams.json` (tab label `tabs.members`)

**Interfaces:**
- Consumes: `MemberConfigPanel` with `:team="selectedTeam"` and `@update-team="onTeamUpdated"`.

- [ ] **Step 1: Implement**

- Add `{ title: tm('tabs.members'), value: 'members' }` to the tab items (next to editor/monitor/history) and `tabs.members` key to all three locales: zh `"成员"`, en `"Members"`, ru `"Участники"`.
- In the tab content area, render `<MemberConfigPanel v-if="tab === 'members'" :team="selectedTeam" @update-team="reloadTeam" />` where `reloadTeam` refreshes the team (reuse the existing team-loading function that the other tabs use).
- Keep the tab list and the existing three tabs' behavior untouched.

- [ ] **Step 2: Verify**

Run: `cd F:/github/Astrbot/dashboard && npx vitest run src/views/AgentTeamsPage.spec.ts src/components/agent_teams/ -q` and `npx vue-tsc --noEmit`
Expected: PASS + 0 type errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/views/AgentTeamsPage.vue dashboard/src/i18n/locales/zh-CN/features/agent-teams.json dashboard/src/i18n/locales/en-US/features/agent-teams.json dashboard/src/i18n/locales/ru-RU/features/agent-teams.json
git commit -m "feat(dashboard): add members tab to agent teams page"
```

---

### Task 7: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Backend checks**

Run: `cd F:/github/Astrbot && ruff format astrbot/core/agent_team_execution.py astrbot/core/astr_main_agent.py astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py astrbot/dashboard/services/agent_team_service.py astrbot/dashboard/services/agent_team_run_service.py astrbot/dashboard/api/agent_teams.py && ruff check .`

- [ ] **Step 2: Frontend checks**

Run: `cd F:/github/Astrbot/dashboard && npx vitest run && npx vue-tsc --noEmit`
Expected: all agent_teams tests green; pre-existing GitDiffSidebar/DocumentManager failures are unrelated (they fail on the base branch too).

- [ ] **Step 3: Commit any formatting leftovers**

```bash
git add -A && git commit -m "style: format agent teams member config changes"
```

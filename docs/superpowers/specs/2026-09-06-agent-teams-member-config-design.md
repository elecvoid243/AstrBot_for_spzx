# Agent Teams 成员配置子页面 (Member Config Page)

**Plan ID:** `2026-09-06-agent-teams-member-config`
**Author:** elecvoid243
**Date:** 2026-09-06
**Status:** Draft
**Parent:** `2026-09-05-agent-teams-design.md`

---

## 1. Executive Summary

Agent Team 成员在创建后目前无法修改（无 update_member API/界面，只能删除重建）。本特性在 Agent Teams 面板新增第 4 个 Tab **「成员」**，包含一个成员配置子页面：成员列表 + 配置表单。表单把 AstrBot「配置文件」页已有的 Agent 执行配置搬过来，支持编辑并**在运行期真实生效**：

- 配置档案（config profile）、人格（Persona，含 Tools/Skills）、Provider
- 工具调用最大轮数（`max_steps`）、工具超时（`tool_call_timeout`）
- 知识库（`kb_names`）、上下文长度（`context_length`）
- 成员名、系统提示词（沿用人格/自定义成员已有字段）

生效机制复用现有「节点 execution 块 → NodeExecutionBinding → 调度器/astr_main_agent」通路：成员配置是节点级 execution 的兜底（节点级优先）。

---

## 2. Current State

- **数据**：成员为 `AgentTeam.members` JSON 列表，字段 `{member_id, name, session_id, umo, persona_id, provider_id, system_prompt}`（`astrbot/core/db/po.py:212-229`）。无独立成员表。
- **服务**：`agent_team_service.py` 只有 `add_member`（272-283）/`remove_member`（285-299），**无 update_member**。`update_team`（233-253）仅改 name/coordinator/config。
- **API**：`dashboard/api/agent_teams.py` 有 POST members / DELETE members，无 PATCH member。前端 `v1.ts` 的 `agentTeamsApi`（1987-2010）同样无成员更新。`useAgentTeams.ts` 无成员 CRUD。
- **运行期**：节点 `execution` 块（`config_id/persona_id/tools/skills`）经 `_validate_node_execution`（agent_team_service.py:416-479）校验后，在 `agent_team_run_service.py::_execute_node`（386-521）注册 `NodeExecutionBinding`（`astrbot/core/agent_team_execution.py:23-46`），`event_bus.py:47-78` 按 `binding.config_id` 选 PipelineScheduler，`astr_main_agent.py::_ensure_persona_and_skills`（542-763）应用 persona/tools/skills 覆盖。**无** provider/max_steps/tool_call_timeout/kb/context 的成员级通道。
- **可复用组件**：`PersonaSelector.vue`、`ProviderSelector.vue`、`KnowledgeBaseSelector.vue`、`PersonaForm.vue`（tools/skills 勾选）、`configProfileApi/personaApi/toolApi/skillApi/providerApi`。
- **配置键**（CONFIG_METADATA_3 / agent_runner 默认）：`max_steps`（工具调用轮数上限）、`tool_call_timeout`（工具调用超时）、`kb_names`（知识库）、`agent_runner.config.model.provider_id`（Provider）、模型 `context_window`（上下文窗口）。

---

## 3. Design

### 3.1 数据模型

`persona_id`、`provider_id`、`system_prompt` 是成员顶层已有字段（`_create_member` 建会话时即 pin 进会话），本特性不改它们的存储位置，仅允许通过 `update_member` 修改并重新 pin 会话。成员 dict 新增一个可选 `runner_config` 块（向后兼容：旧成员无此块 = 沿用现状）：

```python
# member dict 新增键
"runner_config": {
    "config_id": str | None,            # 配置档案 id；存在时 event_bus 按此选调度器
    "tools": list[str] | None,          # 工具白名单；[] = 全部禁用；None = 跟随档案
    "skills": list[str] | None,         # Skill 白名单；语义同 tools
    "max_steps": int | None,            # 工具调用最大轮数（正数，≤ 200）
    "tool_call_timeout": float | None,  # 工具超时秒数（正数，≤ 3600）
    "kb_names": list[str] | None,       # 知识库列表
    "context_length": int | None,       # 上下文长度（token 数，正数，≤ 1000000）
}
```

- 字段留空/缺失 = 跟随配置档案/全局默认（分层：node execution > 成员级 > config_id 档案 > 全局默认）。
- `config_id` 与顶层 `persona_id`/`provider_id` 互不冲突：config_id 决定调度器（整体配置），persona_id/tools/skills 作为调度覆盖（与现有节点 execution 语义一致）。
- 成员级 persona/provider 来源：顶层 `member["persona_id"]` / `member["provider_id"]`（与现状一致），不改底层字段名。

### 3.2 后端

**`astrbot/dashboard/services/agent_team_service.py`**：
- 新增 `update_member(username, team_id, member_id, payload)`：
  - 校验 team 存在、成员存在（dup 名检查复用 add_member 规则，排除自身）。
  - 顶层字段：`name`（≤ MAX_NAME_LEN）、`persona_id`（存在性校验，与 `_create_member` 相同）、`provider_id`、`system_prompt`。
  - `runner_config`：新增 `_validate_member_runner_config`，复用 `_validate_node_execution` 的 config_id/tools/skills 规则（profile 存在于 `core_lifecycle.astrbot_config_mgr.confs`、tools/skills 为 `list[str] | None`），另加数值范围校验（max_steps/tool_call_timeout/context_length 正数 + 上限，kb_names 为 `list[str]`）。
  - **会话重新 pin**：`persona_id`/`provider_id`/`system_prompt` 变化时，复用 `_create_member` 的 pin 逻辑（`conversation_manager.new_conversation(...)` / `provider_manager.set_provider(...)`）对 `member["session_id"]` 重新 pin，确保下次运行立即生效。
  - 通过 `update_agent_team` 写回；返回更新后的 team。
- `_execute_node`（agent_team_run_service.py）合并：成员级 = `{**member.get("runner_config", {}), "persona_id": member.get("persona_id") or None, "provider_id": member.get("provider_id") or None}`；执行级 = `{**成员级, **node.get("execution", {})}`（节点优先），把扩展字段传入绑定。

**`astrbot/dashboard/api/agent_teams.py`**：
- 新增 `PATCH /agent_teams/{team_id}/members/{member_id}` → `update_member`；401/404/409 错误语义与现有 members 端点一致。

**`astrbot/core/agent_team_execution.py`**：
- `NodeExecutionBinding` 增加 `provider_id`、`max_steps`、`tool_call_timeout`、`kb_names`、`context_length`（均为可空）。`persona_id`/`tools`/`skills` 已有。

**运行期生效**：
- `event_bus.py`：调度器选择逻辑不变（config_id 存在 → 该档案 PipelineScheduler；无 → umo 路由）。绑定字段原样传递。
- `astr_main_agent.py`：在 `_ensure_persona_and_skills`（542-763）与请求级 config 组装处，把绑定中的覆盖叠加到该请求的 runner config（在 persona/tools/skills 覆盖同一注入点）：`max_steps`→misc、`tool_call_timeout`→misc、`kb_names`→rag、`context_length`→模型上下文窗口上限、`provider_id`→该次请求的 provider。实现时以「对请求 cfg 副本做覆盖」而不是改全局配置。

### 3.3 前端

**`dashboard/src/views/AgentTeamsPage.vue`**：
- Tab 栏新增「成员」标签页（工作流编排 | 运行监控 | 历史 | **成员**），渲染 `MemberConfigPanel`。

**新组件 `dashboard/src/components/agent_teams/MemberConfigPanel.vue`**：
- 左侧成员列表（复用 `collabMemberColor`、协调者徽标、会话忙状态提示沿用现有成员列表样式），点击选中。
- 右侧 `MemberConfigForm`，切换成员自动加载其 `runner_config`。

**`MemberConfigForm.vue`**：
- 字段：成员名、配置档案（`configProfileApi.list()`）、人格（`PersonaSelector`，随后展开 Tools/Skills 复选，与 `PersonaForm` 的 `v-model:tools`/`:skills` 交互一致）、Provider（`providerApi.listByProviderType('chat_completion')`）、工具调用最大轮数/工具超时/上下文长度（数字输入，带范围 hint）、知识库（`KnowledgeBaseSelector`）。
- 保存 → `agentTeamsApi.updateMember(teamId, memberId, payload)` → toast + 刷新团队数据。
- 不加载配置档案选项/知识库/工具列表时的失败 toast（沿用现有 `extractApiError` 模式）。

**`dashboard/src/api/v1.ts`**：
- `agentTeamsApi` 增加 `updateMember(teamId, memberId, payload)`（手写 facade，与 addMember/removeMember 同级）。

**i18n**：`agent-teams.json`（zh-CN/en-US/ru-RU 三语对齐）新增成员页键（标题、字段标签、提示、确认等）。

### 3.4 合并语义（运行期）

1. 节点 `execution` 块中的字段 > 成员级字段（`member["persona_id"|"provider_id"]` + `member["runner_config"]` 的其余字段）。
2. 成员级字段 > `config_id` 档案中的同名字段（覆盖为请求级 cfg 副本）。
3. 全部缺失 > 全局默认/会话默认。
4. 删除成员、解散团队的现有行为不变；`remove_member` 的忙会话/进行中运行保护不变。

---

## 4. Testing

- **后端**：`update_member` 单测（校验通过/失败：config_id 不存在、persona 不存在、tools/skills 非法、数值越界、成员不存在、忙会话不改名）、合并优先级单测（node execution > runner_config > 档案）、向后兼容（无 runner_config 的旧成员可正常跑）。
- **前端**：`MemberConfigPanel.spec`（成员列表渲染、选中、表单回填）、`MemberConfigForm.spec`（保存 payload、Persona/Tools/Skills 交互、错误 toast）；现有 agent_teams 154 测试保持全绿。
- **运行链**：绑定携带扩展字段的回归单测（mock 优先，不启真实 LLM）。

## 5. Risks & Mitigations

- **运行链改动风险**（astr_main_agent/event_bus）：所有覆盖只作用于请求级 cfg 副本，不碰全局/会话配置；改动点与现有 persona/tools/skills 覆盖同一位置，测试覆盖「字段透传」而不点真模型。
- **JSON 兼容**：runner_config 完全可选；旧数据零迁移。
- **上下文长度语义**：不同 provider 的模型上下文窗口来源不同，覆盖实现以「请求级模型配置覆盖」为准，不写死特定 provider。

---

**End of Document**

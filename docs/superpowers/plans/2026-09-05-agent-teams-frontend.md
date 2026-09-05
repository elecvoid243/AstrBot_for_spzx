# Agent Teams Frontend (Plan 2/3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Agent Teams dashboard panel: team/member management, a ComfyUI-style DAG workflow editor (Vue Flow), and a multi-pane run monitor (adaptive resizable grid of per-agent windows + DAG progress view) fed by run SSE streams.

**Architecture:** One new route `/agent-teams` → `views/AgentTeamsPage.vue` (sidebar + 3 tabs). State lives in two module-singleton composables (`useAgentTeams` CRUD, `useAgentTeamsRun` run lifecycle + SSE) following the `useAgentCollab` precedent; SSE event folding is a pure leaf reducer (`agentTeamsRunReducer.ts`) tested without DOM. The 20 backend routes are added to `openspec/openapi-v1.yaml`, the client regenerated (`pnpm generate:api`), and consumed through a new `agentTeamsApi` facade in `src/api/v1.ts`.

**Tech Stack:** Vue 3.3 SFC `<script setup lang="ts">`, Vuetify 3.7 (auto-import), `@vue-flow/core` (new dep), `grid-layout-plus` (new dep), vitest + happy-dom + @vue/test-utils, pnpm.

**Spec:** `docs/superpowers/specs/2026-09-05-agent-teams-design.md` (§7 API, §8 frontend; auto-orchestration UI + Collab retirement are Plan 3 — auto mode appears in the UI as a disabled option).

## Global Constraints

- **Vue 3.3: NEVER use `defineModel`** — the project's SFC compiler does not transform it; use explicit `modelValue` prop + `update:modelValue` emit (documented precedent: `CollabBindDialog.vue:57-59`).
- Feature state in module-singleton composables (like `useAgentCollab.ts`), NOT pinia.
- i18n: all user-visible strings via `useModuleI18n('features/agent-teams')`; keys must exist in ALL THREE locales (`zh-CN`, `en-US`, `ru-RU`) — `i18n.completeness.spec.ts` enforces parity.
- New tests are `*.spec.ts` (vitest includes only `*.spec.ts`); pure-logic reducers go in dependency-free leaf modules tested like `subagentRunReducer.spec.ts`.
- Vuetify dialogs: title base class `text-h3 pa-4 pb-0 pl-6`; buttons `variant="text"` or `variant="tonal"` (AGENTS.md).
- English code comments; pnpm only; conventional commits (`feat(dashboard): ...`).
- SSE consumption: `fetchWithAuth` + `Accept: text/event-stream` + `AbortController` (never EventSource for new code).
- Working directory for all frontend commands: `dashboard/`.

---

### Task 1: Dependencies + i18n scaffolding

**Files:**
- Modify: `dashboard/package.json` (via pnpm add)
- Create: `dashboard/src/i18n/locales/zh-CN/features/agent-teams.json`, `dashboard/src/i18n/locales/en-US/features/agent-teams.json`, `dashboard/src/i18n/locales/ru-RU/features/agent-teams.json`
- Modify: `dashboard/src/i18n/translations.ts` (3 imports + 3 feature-map entries)
- Modify: `dashboard/src/i18n/loader.ts` (registerModules list)
- Modify: `dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU}/core/navigation.json` (one key each)

**Interfaces:**
- Produces: i18n module `features/agent-teams` with the key set used by every later task (page/tabs/teams/members/editor/monitor/history groups).

- [ ] **Step 1: Install deps**

```bash
cd dashboard && pnpm add @vue-flow/core grid-layout-plus
```

- [ ] **Step 2: Create `zh-CN/features/agent-teams.json`**

```json
{
  "page": {
    "title": "Agent 团队",
    "beta": "Beta",
    "refresh": "刷新"
  },
  "tabs": { "editor": "工作流编排", "monitor": "运行监控", "history": "历史" },
  "teams": {
    "create": "新建团队",
    "empty": "暂无团队，点击“新建团队”创建",
    "name": "团队名称",
    "delete": "解散团队",
    "deleteConfirm": "确定解散该团队？成员会话将保留",
    "coordinator": "协调者",
    "coordinatorSwitch": "设为协调者",
    "config": "团队配置",
    "failurePolicy": "失败策略",
    "failurePolicyPause": "暂停等待处理",
    "failurePolicyAutoSkip": "自动跳过并继续",
    "replyTimeout": "单回合超时（秒）",
    "maxRounds": "最大轮次",
    "maxParallel": "最大并行数",
    "injectMaxLength": "结果注入长度上限"
  },
  "members": {
    "title": "成员",
    "add": "添加成员",
    "remove": "移除",
    "empty": "至少需要 2 名成员",
    "name": "成员名称",
    "fromPersona": "从人格创建",
    "custom": "自定义创建",
    "persona": "人格",
    "systemPrompt": "系统提示词",
    "provider": "模型（可选）",
    "providerDefault": "使用会话默认模型"
  },
  "editor": {
    "workflowName": "工作流名称",
    "save": "保存工作流",
    "addNode": "添加节点",
    "deleteNode": "删除选中",
    "nodeMember": "执行成员",
    "nodeTask": "任务模板",
    "nodeTaskHint": "支持变量：{{input}} 运行输入、{{节点ID}} 引用前驱节点结果",
    "insertInput": "插入 {{input}}",
    "validation": "校验",
    "selectNode": "点击节点编辑任务",
    "missingMember": "有节点绑定的成员已被移除",
    "monitorMode": "运行视图"
  },
  "monitor": {
    "input": "任务目标",
    "inputPlaceholder": "描述这次团队要完成的目标…",
    "workflow": "工作流（手动编排）",
    "noWorkflow": "无",
    "start": "开始运行",
    "pause": "暂停",
    "resume": "继续",
    "stop": "停止",
    "resumeInterrupted": "恢复运行",
    "autoModeDisabled": "自动编排将在后续版本开放",
    "status": { "idle": "空闲", "running": "运行中", "paused": "已暂停", "stopped": "已停止", "completed": "已完成", "failed": "失败", "interrupted": "已中断" },
    "node": { "pending": "待执行", "running": "执行中", "done": "完成", "failed": "失败", "skipped": "已跳过" },
    "retryNode": "重试该节点",
    "skipNode": "跳过并级联跳过",
    "dagProgress": "DAG 进度",
    "busy": "会话忙，等待中…",
    "empty": "输入目标并点击“开始运行”"
  },
  "history": {
    "title": "运行历史",
    "empty": "暂无运行记录",
    "time": "时间",
    "mode": "模式",
    "input": "目标",
    "status": "状态"
  },
  "errors": { "loadFailed": "加载失败", "saveFailed": "保存失败", "operationFailed": "操作失败" }
}
```

- [ ] **Step 3: Create `en-US/features/agent-teams.json`** — same key tree, English values (`"Agent Teams"`, `"Workflow Editor"`, `"Run Monitor"`, `"History"`, `"New Team"`, statuses `"Idle/Running/Paused/Stopped/Completed/Failed/Interrupted"`, node states `"Pending/Running/Done/Failed/Skipped"`, etc.). Key parity is what the completeness spec enforces; wording just needs to be sensible.

- [ ] **Step 4: Create `ru-RU/features/agent-teams.json`** — same key tree, Russian values (`"Команды агентов"`, `"Редактор workflows"`, `"Мониторинг запуска"`, `"История"`, statuses `"Простой/Выполняется/Пауза/Остановлен/Завершён/Ошибка/Прерван"`).

- [ ] **Step 5: Register in `translations.ts` + `loader.ts`**

In `translations.ts`, follow the existing per-locale pattern (import near the other feature imports, add to the `features` map): `import zhCNAgentTeams from './locales/zh-CN/features/agent-teams.json';` … `agentTeams: zhCNAgentTeams,` inside each locale's `features` object. In `loader.ts`, add the module to `registerModules()` following the existing entry shape (`'features/agent-teams'` → the three locale JSONs), copying whatever shape neighboring entries use.

- [ ] **Step 6: Navigation key** — in each locale's `core/navigation.json` add `"agentTeams": "Agent 团队" / "Agent Teams" / "Команды агентов"` (next to the existing `subagent` key).

- [ ] **Step 7: Verify parity**

Run: `cd dashboard && pnpm exec vitest run src/i18n` — expect all i18n specs (incl. `i18n.completeness.spec.ts`) PASS.

- [ ] **Step 8: Commit**

```bash
git add dashboard/package.json dashboard/pnpm-lock.yaml dashboard/src/i18n/
git commit -m "feat(dashboard): add agent teams i18n scaffolding and deps"
```

---

### Task 2: OpenAPI paths + client regeneration + `agentTeamsApi` facade

**Files:**
- Modify: `openspec/openapi-v1.yaml` (append paths block + component schemas)
- Regenerate: `dashboard/src/api/generated/openapi-v1/` (via `pnpm generate:api`)
- Modify: `dashboard/src/api/v1.ts` (append `agentTeamsApi` facade)

**Interfaces:**
- Produces: `agentTeamsApi` facade with methods (all returning `Promise<AxiosResponse<ApiEnvelope<T>>>`): `listTeams()`, `createTeam(payload)`, `getTeam(teamId)`, `updateTeam(teamId, payload)`, `deleteTeam(teamId)`, `addMember(teamId, payload)`, `removeMember(teamId, memberId)`, `listWorkflows(teamId)`, `createWorkflow(teamId, payload)`, `updateWorkflow(teamId, workflowId, payload)`, `deleteWorkflow(teamId, workflowId)`, `startRun(teamId, payload)`, `listActiveRuns()`, `listTeamRuns(teamId)`, `pauseRun(runId)`, `resumeRun(runId)`, `stopRun(runId)`, `retryNode(runId, nodeId)`, `skipNode(runId, nodeId)`, plus `runStreamUrl(runId)`.
- `listActiveRuns()` returns runs WITH snapshots (`{run_id, status, node_states, progress, graph…}`) — the monitor seeds its reducer state from these.

- [ ] **Step 1: Append paths to `openspec/openapi-v1.yaml`**

Insert before `components:` (match the file's existing 2-space path indentation, `x-astrbot-scope: chat` on every operation, `$ref: "#/components/responses/Ok"` responses, camelCase `operationId`s):

```yaml
  /api/v1/agent_teams:
    get:
      tags: [AgentTeams]
      summary: List agent teams owned by the current user
      operationId: listAgentTeams
      x-astrbot-scope: chat
      responses:
        "200":
          $ref: "#/components/responses/Ok"
    post:
      tags: [AgentTeams]
      summary: Create a team and all member sessions
      operationId: createAgentTeam
      x-astrbot-scope: chat
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/AgentTeamCreatePayload"
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/agent_teams/{team_id}:
    get:
      tags: [AgentTeams]
      summary: Get one team
      operationId: getAgentTeam
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/TeamIdPath"
      responses:
        "200":
          $ref: "#/components/responses/Ok"
    patch:
      tags: [AgentTeams]
      summary: Update team name/coordinator/config
      operationId: updateAgentTeam
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/TeamIdPath"
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/AgentTeamUpdatePayload"
      responses:
        "200":
          $ref: "#/components/responses/Ok"
    delete:
      tags: [AgentTeams]
      summary: Delete a team (member sessions are preserved)
      operationId: deleteAgentTeam
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/TeamIdPath"
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/agent_teams/{team_id}/members:
    post:
      tags: [AgentTeams]
      summary: Add a member (creates its WebChat session server-side)
      operationId: addAgentTeamMember
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/TeamIdPath"
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/AgentTeamMemberPayload"
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/agent_teams/{team_id}/members/{member_id}:
    delete:
      tags: [AgentTeams]
      summary: Remove a member
      operationId: removeAgentTeamMember
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/TeamIdPath"
        - $ref: "#/components/parameters/MemberIdPath"
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/agent_teams/{team_id}/workflows:
    get:
      tags: [AgentTeams]
      summary: List the team's workflow templates
      operationId: listAgentTeamWorkflows
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/TeamIdPath"
      responses:
        "200":
          $ref: "#/components/responses/Ok"
    post:
      tags: [AgentTeams]
      summary: Create a workflow template
      operationId: createAgentTeamWorkflow
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/TeamIdPath"
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/AgentTeamWorkflowPayload"
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/agent_teams/{team_id}/workflows/{workflow_id}:
    put:
      tags: [AgentTeams]
      summary: Update a workflow template (graph + layout, server-validated)
      operationId: updateAgentTeamWorkflow
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/TeamIdPath"
        - $ref: "#/components/parameters/WorkflowIdPath"
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/AgentTeamWorkflowPayload"
      responses:
        "200":
          $ref: "#/components/responses/Ok"
    delete:
      tags: [AgentTeams]
      summary: Delete a workflow template
      operationId: deleteAgentTeamWorkflow
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/TeamIdPath"
        - $ref: "#/components/parameters/WorkflowIdPath"
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/agent_teams/{team_id}/runs:
    get:
      tags: [AgentTeams]
      summary: List the team's run history (full rows incl. graph/node_states)
      operationId: listAgentTeamRuns
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/TeamIdPath"
      responses:
        "200":
          $ref: "#/components/responses/Ok"
    post:
      tags: [AgentTeams]
      summary: Start a run (409 when the team already has an active run)
      operationId: startAgentTeamRun
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/TeamIdPath"
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/AgentTeamRunStartPayload"
      responses:
        "200":
          $ref: "#/components/responses/Ok"
        "409":
          $ref: "#/components/responses/Error"

  /api/v1/agent_teams/runs/active:
    get:
      tags: [AgentTeams]
      summary: List the user's active runs with snapshots
      operationId: listActiveAgentTeamRuns
      x-astrbot-scope: chat
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/agent_teams/runs/{run_id}/pause:
    post:
      tags: [AgentTeams]
      summary: Pause a run
      operationId: pauseAgentTeamRun
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/RunIdPath"
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/agent_teams/runs/{run_id}/resume:
    post:
      tags: [AgentTeams]
      summary: Resume a paused or interrupted run
      operationId: resumeAgentTeamRun
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/RunIdPath"
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/agent_teams/runs/{run_id}/stop:
    post:
      tags: [AgentTeams]
      summary: Stop a run
      operationId: stopAgentTeamRun
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/RunIdPath"
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/agent_teams/runs/{run_id}/nodes/{node_id}/retry:
    post:
      tags: [AgentTeams]
      summary: Retry a failed node
      operationId: retryAgentTeamNode
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/RunIdPath"
        - $ref: "#/components/parameters/NodeIdPath"
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/agent_teams/runs/{run_id}/nodes/{node_id}/skip:
    post:
      tags: [AgentTeams]
      summary: Skip a failed node and cascade-skip its successors
      operationId: skipAgentTeamNode
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/RunIdPath"
        - $ref: "#/components/parameters/NodeIdPath"
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/agent_teams/runs/{run_id}/stream:
    get:
      tags: [AgentTeams]
      summary: Server-sent events for one run (full history replay, then live, heartbeats)
      operationId: streamAgentTeamRun
      x-astrbot-scope: chat
      parameters:
        - $ref: "#/components/parameters/RunIdPath"
      responses:
        "200":
          description: Server-sent run event stream or an error envelope
          content:
            text/event-stream:
              schema:
                type: string
```

Add to `components/parameters` (follow the file's existing parameter section style):

```yaml
    TeamIdPath:
      name: team_id
      in: path
      required: true
      schema: { type: string }
    MemberIdPath:
      name: member_id
      in: path
      required: true
      schema: { type: string }
    WorkflowIdPath:
      name: workflow_id
      in: path
      required: true
      schema: { type: string }
    RunIdPath:
      name: run_id
      in: path
      required: true
      schema: { type: string }
    NodeIdPath:
      name: node_id
      in: path
      required: true
      schema: { type: string }
```

Add to `components/schemas`:

```yaml
    AgentTeamMemberPayload:
      type: object
      required: [name]
      properties:
        name: { type: string }
        persona_id: { type: [string, "null"] }
        system_prompt: { type: [string, "null"] }
        provider_id: { type: [string, "null"] }

    AgentTeamCreatePayload:
      type: object
      required: [name, members, coordinator]
      properties:
        name: { type: string }
        members:
          type: array
          items: { $ref: "#/components/schemas/AgentTeamMemberPayload" }
        coordinator: { type: string }
        config: { type: object, additionalProperties: true }

    AgentTeamUpdatePayload:
      type: object
      additionalProperties: true

    WorkflowNode:
      type: object
      required: [id, member_id, task]
      properties:
        id: { type: string }
        member_id: { type: string }
        task: { type: string }
        title: { type: [string, "null"] }

    WorkflowEdge:
      type: object
      required: [from, to]
      properties:
        from: { type: string }
        to: { type: string }

    WorkflowGraph:
      type: object
      properties:
        nodes:
          type: array
          items: { $ref: "#/components/schemas/WorkflowNode" }
        edges:
          type: array
          items: { $ref: "#/components/schemas/WorkflowEdge" }

    AgentTeamWorkflowPayload:
      type: object
      required: [name, graph]
      properties:
        name: { type: string }
        graph: { $ref: "#/components/schemas/WorkflowGraph" }
        layout:
          type: object
          additionalProperties:
            type: object
            additionalProperties: { type: number }

    AgentTeamRunStartPayload:
      type: object
      required: [mode, input]
      properties:
        mode: { type: string, enum: [auto, dag] }
        input: { type: string }
        workflow_id: { type: [string, "null"] }
```

- [ ] **Step 2: Regenerate the client**

Run: `cd dashboard && pnpm generate:api`
Expected: `src/api/generated/openapi-v1/{index,sdk.gen,types.gen}.ts` regenerated; `sdk.gen.ts` contains `createAgentTeam`, `startAgentTeamRun`, `streamAgentTeamRun`, etc. If generation fails on YAML errors, fix per the validator output (indentation/ref names) — do not hand-edit generated files.

- [ ] **Step 3: Append the facade to `dashboard/src/api/v1.ts`**

Follow the `agentCollabApi` file-tail pattern (raw httpClient, envelope type), since our backend handlers return `JSONResponse` envelopes identical to collab's:

```ts
export const agentTeamsApi = {
  listTeams() {
    return httpClient.get<ApiEnvelope<any>>('/api/v1/agent_teams');
  },
  createTeam(payload: any) {
    return httpClient.post<ApiEnvelope<any>>('/api/v1/agent_teams', payload);
  },
  getTeam(teamId: string) {
    return httpClient.get<ApiEnvelope<any>>(`/api/v1/agent_teams/${encodeURIComponent(teamId)}`);
  },
  updateTeam(teamId: string, payload: any) {
    return httpClient.patch<ApiEnvelope<any>>(`/api/v1/agent_teams/${encodeURIComponent(teamId)}`, payload);
  },
  deleteTeam(teamId: string) {
    return httpClient.delete<ApiEnvelope<any>>(`/api/v1/agent_teams/${encodeURIComponent(teamId)}`);
  },
  addMember(teamId: string, payload: any) {
    return httpClient.post<ApiEnvelope<any>>(`/api/v1/agent_teams/${encodeURIComponent(teamId)}/members`, payload);
  },
  removeMember(teamId: string, memberId: string) {
    return httpClient.delete<ApiEnvelope<any>>(
      `/api/v1/agent_teams/${encodeURIComponent(teamId)}/members/${encodeURIComponent(memberId)}`
    );
  },
  listWorkflows(teamId: string) {
    return httpClient.get<ApiEnvelope<any>>(`/api/v1/agent_teams/${encodeURIComponent(teamId)}/workflows`);
  },
  createWorkflow(teamId: string, payload: any) {
    return httpClient.post<ApiEnvelope<any>>(`/api/v1/agent_teams/${encodeURIComponent(teamId)}/workflows`, payload);
  },
  updateWorkflow(teamId: string, workflowId: string, payload: any) {
    return httpClient.put<ApiEnvelope<any>>(
      `/api/v1/agent_teams/${encodeURIComponent(teamId)}/workflows/${encodeURIComponent(workflowId)}`,
      payload
    );
  },
  deleteWorkflow(teamId: string, workflowId: string) {
    return httpClient.delete<ApiEnvelope<any>>(
      `/api/v1/agent_teams/${encodeURIComponent(teamId)}/workflows/${encodeURIComponent(workflowId)}`
    );
  },
  startRun(teamId: string, payload: { mode: string; input: string; workflow_id?: string | null }) {
    return httpClient.post<ApiEnvelope<any>>(`/api/v1/agent_teams/${encodeURIComponent(teamId)}/runs`, payload);
  },
  listActiveRuns() {
    return httpClient.get<ApiEnvelope<any>>('/api/v1/agent_teams/runs/active');
  },
  listTeamRuns(teamId: string) {
    return httpClient.get<ApiEnvelope<any>>(`/api/v1/agent_teams/${encodeURIComponent(teamId)}/runs`);
  },
  pauseRun(runId: string) {
    return httpClient.post<ApiEnvelope<any>>(`/api/v1/agent_teams/runs/${encodeURIComponent(runId)}/pause`);
  },
  resumeRun(runId: string) {
    return httpClient.post<ApiEnvelope<any>>(`/api/v1/agent_teams/runs/${encodeURIComponent(runId)}/resume`);
  },
  stopRun(runId: string) {
    return httpClient.post<ApiEnvelope<any>>(`/api/v1/agent_teams/runs/${encodeURIComponent(runId)}/stop`);
  },
  retryNode(runId: string, nodeId: string) {
    return httpClient.post<ApiEnvelope<any>>(
      `/api/v1/agent_teams/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/retry`
    );
  },
  skipNode(runId: string, nodeId: string) {
    return httpClient.post<ApiEnvelope<any>>(
      `/api/v1/agent_teams/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/skip`
    );
  },
  runStreamUrl(runId: string) {
    return `/api/v1/agent_teams/runs/${encodeURIComponent(runId)}/stream`;
  },
};
```

Note: keep the generated client regeneration (Step 2) in this commit even though the facade uses raw paths — the yaml is now the contract of record for Plan 3's client consumption and `pnpm generate:api` stays honest. If the generated output conflicts with lint rules, exclude `src/api/generated/` the way existing config does (check `eslint`/`vitest` ignore lists — do not modify generated files by hand).

- [ ] **Step 4: Verify**

Run: `cd dashboard && pnpm exec vitest run src/api src/i18n 2>/dev/null | tail -5; pnpm build 2>&1 | tail -5`
Expected: existing specs pass; production build succeeds (type-checks the new facade).

- [ ] **Step 5: Commit**

```bash
git add openspec/openapi-v1.yaml dashboard/src/api/
git commit -m "feat(dashboard): add agent teams OpenAPI contract and API facade"
```

---

### Task 3: Run-event types + pure reducer leaf

**Files:**
- Create: `dashboard/src/composables/agentTeamsRunReducer.ts`
- Create: `dashboard/src/composables/agentTeamsRunReducer.spec.ts`

**Interfaces:**
- Produces (consumed by Task 5's composable and Task 8's components):
  - `type TeamsRunEvent` — discriminated union parsed from SSE JSON: `message {direction: 'sent'|'stream'|'reply', member_id, session_id, text, parts?}`, `node_status {node_id, member_id?, status, error?}`, `dag_progress {done,running,pending,skipped,failed,total}`, `round {n,max}`, `dispatch {assignments}`, `busy {session_id}`, `paused {reason, node_id?}`, `error {reason}`, `stopped {reason}`.
  - `type MemberWindowState { memberId, sent: string | null, streamText: string, parts: any[], streaming: boolean }`
  - `type TeamsRunState { runId, status: string, progress, round, windows: Record<string, MemberWindowState>, nodeStates: Record<string, {status, error?}>, busySessionIds: Set<string>, pausedNodeId: string | null, lastError: string | null, stoppedReason: string | null }`
  - `createTeamsRunState(runId: string, seed?: {status?, nodeStates?, graph?}): TeamsRunState` — seeds `nodeStates` from a run snapshot/history row.
  - `applyTeamsEvent(state: TeamsRunEvent-parsed-plain, ev: TeamsRunEvent): void` — mutates `state` in place (reactive-host pattern like `subagentRunReducer`).
  - `parseTeamsEvent(raw: any): TeamsRunEvent | null`.
  - Fold rules: `message sent` → set `windows[member_id].sent = text` (clear streamText); `message stream` → append delta to `streamText` (create window lazily), `streaming = true`; `message reply` → append full text as `streamText` final value (`streamText = text` when it extends the stream, else set), `streaming = false`, keep `parts`; `node_status` → update `nodeStates[node_id]`; `dag_progress` → replace `progress`; `busy` → add/remove session from `busySessionIds` (`busy` events are emitted repeatedly while waiting — treat as "waiting" marker, add only); `paused` → `status = 'paused'`, `pausedNodeId`; `error` → `lastError`; `stopped` → `status = 'stopped'` unless already terminal, `stoppedReason`.

- [ ] **Step 1: Write the failing spec** (`agentTeamsRunReducer.spec.ts`) — vitest, no DOM:

```ts
import { describe, expect, it } from 'vitest';
import { applyTeamsEvent, createTeamsRunState, parseTeamsEvent } from './agentTeamsRunReducer';

describe('agentTeamsRunReducer', () => {
  it('parses known SSE payloads and rejects unknown ones', () => {
    expect(parseTeamsEvent({ type: 'message', direction: 'sent', member_id: 'm1', text: 'hi' })?.type).toBe('message');
    expect(parseTeamsEvent({ type: 'heartbeat' })).toBeNull();
  });

  it('folds sent → stream deltas → reply into one member window', () => {
    const s = createTeamsRunState('r1');
    applyTeamsEvent(s, parseTeamsEvent({ type: 'message', direction: 'sent', member_id: 'm1', session_id: 'c1', text: '任务' })!);
    applyTeamsEvent(s, parseTeamsEvent({ type: 'message', direction: 'stream', member_id: 'm1', text: '你好' })!);
    applyTeamsEvent(s, parseTeamsEvent({ type: 'message', direction: 'stream', member_id: 'm1', text: '，世界' })!);
    expect(s.windows['m1']).toMatchObject({ sent: '任务', streamText: '你好，世界', streaming: true });
    applyTeamsEvent(s, parseTeamsEvent({ type: 'message', direction: 'reply', member_id: 'm1', text: '你好，世界！' })!);
    expect(s.windows['m1']).toMatchObject({ streamText: '你好，世界！', streaming: false });
  });

  it('tracks node states, progress and terminal status', () => {
    const s = createTeamsRunState('r1', { nodeStates: { n1: { status: 'pending' }, n2: { status: 'pending' } } });
    applyTeamsEvent(s, parseTeamsEvent({ type: 'node_status', node_id: 'n1', status: 'running' })!);
    applyTeamsEvent(s, parseTeamsEvent({ type: 'dag_progress', done: 0, running: 1, pending: 1, skipped: 0, failed: 0, total: 2 })!);
    applyTeamsEvent(s, parseTeamsEvent({ type: 'node_status', node_id: 'n1', status: 'done' })!);
    expect(s.nodeStates['n1'].status).toBe('done');
    expect(s.progress).toMatchObject({ running: 0, done: 1 });
    applyTeamsEvent(s, parseTeamsEvent({ type: 'paused', reason: 'node failed', node_id: 'n2' })!);
    expect(s.status).toBe('paused');
    expect(s.pausedNodeId).toBe('n2');
    applyTeamsEvent(s, parseTeamsEvent({ type: 'stopped', reason: 'done' })!);
    expect(s.status).toBe('stopped');
  });
});
```

Add 2 more tests in the same file: lazy window creation on a bare `stream` event; `reply` on a window with NO prior stream sets `streamText` directly and `streaming=false`.

- [ ] **Step 2: Run to verify failure** — `pnpm exec vitest run src/composables/agentTeamsRunReducer.spec.ts` → FAIL (module not found).

- [ ] **Step 3: Implement the reducer** — dependency-free leaf module (no `@/api`, no vue imports), in-place mutation per the `subagentRunReducer` precedent. Implement the types and fold rules from Interfaces exactly; `parseTeamsEvent` returns `null` for unknown `type`s and non-object inputs; `busy` events add the session to `busySessionIds` and never remove (the runner emits them only while waiting; `reply` for that member is the natural clear signal — also clear a member's sessions on that member's `reply`).

- [ ] **Step 4: Run to verify pass** — expect all reducer specs PASS; run the full `pnpm exec vitest run src/composables` to confirm no regressions.

- [ ] **Step 5: Commit** — `git add dashboard/src/composables/agentTeamsRunReducer* && git commit -m "feat(dashboard): add agent teams run event reducer"`

---

### Task 4: `useAgentTeams` + `useAgentTeamsRun` composables

**Files:**
- Create: `dashboard/src/composables/useAgentTeams.ts`
- Create: `dashboard/src/composables/useAgentTeams.spec.ts`
- Create: `dashboard/src/composables/useAgentTeamsRun.ts`
- Create: `dashboard/src/composables/useAgentTeamsRun.spec.ts`

**Interfaces:**
- `useAgentTeams()` (module-singleton, `useAgentCollab` pattern): `{ teams, selectedTeamId, selectedTeam, workflows, loadTeams, selectTeam(id), createTeam(payload), updateTeam(id, payload), deleteTeam(id), loadWorkflows(teamId), saveWorkflow(teamId, wf), deleteWorkflow(teamId, workflowId) }`. All actions toast on `res.data.status === 'error'` via `useToast()` and re-throw nothing.
- `useAgentTeamsRun()`: `{ runState: Ref<TeamsRunState | null>, monitors: Ref<active run summaries>, loadActiveRuns, openRun(runId), closeRun(), startRun(teamId, payload), pause(runId), resumeRun(runId), stop(runId), retryNode(nodeId), skipNode(nodeId), reconnect() }`. `openRun` seeds state (from `listActiveRuns` snapshot, falling back to `listTeamRuns` history rows) then attaches SSE: `fetchWithAuth(agentTeamsApi.runStreamUrl(runId), { headers: { Accept: 'text/event-stream' }, signal })` → buffer on `"\n\n"` → `parseTeamsEvent(JSON.parse(data))` → `applyTeamsEvent`. Heartbeats (`:` comment lines / empty data) are skipped by the parser. Auto-reconnect: up to 5 attempts, 1s backoff (copy `useMessages.startSystemStream` shape). Only one run attached at a time (`closeRun` aborts + nulls state).

- [ ] **Step 1: Write `useAgentTeams.spec.ts`** — `vi.mock('@/api/v1', ...)` with `agentTeamsApi` mocks returning `{ data: { status: 'ok', data: {...} } }` envelopes; invoke the composable directly (no host component needed); assert `loadTeams` populates `teams`, `selectTeam` switches `selectedTeam`, `createTeam` calls the API and refreshes the list, error envelope triggers `toast.error` (mock `@/utils/toast`).
- [ ] **Step 2: Write `useAgentTeamsRun.spec.ts`** — mock `@/api/http`'s `fetchWithAuth` to return a fake `Response`-like `{ ok: true, body: ReadableStream }` built from a `TextEncoder` pumping three framed events (`sent`, `stream`, `reply`); assert `runState.windows` folds correctly and `closeRun()` aborts (mock `fetchWithAuth` capturing the `AbortSignal`, assert `signal.aborted` after close). Mock `agentTeamsApi.startRun` returning a snapshot and assert `startRun` seeds + attaches.
- [ ] **Step 3: Run to verify failure** (`pnpm exec vitest run src/composables/useAgentTeams*.spec.ts`) → FAIL.
- [ ] **Step 4: Implement both composables.** For the SSE reader, copy the `readCollabStream` buffering approach (`useAgentCollab.ts:80-107`) into a private function in `useAgentTeamsRun.ts`. SSE framing detail: the backend heartbeats with `: heartbeat\n\n` comment lines — the buffer splitter must ignore events whose joined `data:` is empty.
- [ ] **Step 5: Run to verify pass** — new specs green, `pnpm exec vitest run src/composables` all green.
- [ ] **Step 6: Commit** — `git commit -m "feat(dashboard): add agent teams composables"`

---

### Task 5: Router, navigation, page scaffold + sidebar

**Files:**
- Modify: `dashboard/src/router/MainRoutes.ts` (one child route)
- Modify: `dashboard/src/layouts/full/vertical-sidebar/sidebarItem.ts` (one entry in the `more` group)
- Create: `dashboard/src/views/AgentTeamsPage.vue`
- Create: `dashboard/src/components/agent_teams/AgentTeamsSidebar.vue`
- Create: `dashboard/src/views/AgentTeamsPage.spec.ts`

**Interfaces:**
- Route: `{ name: 'AgentTeams', path: '/agent-teams', component: () => import('@/views/AgentTeamsPage.vue') }` in `MainRoutes` children (copy the `SubAgent` entry at `MainRoutes.ts:223-227`); sidebar entry `{ title: 'core.navigation.agentTeams', icon: 'mdi-account-group', to: '/agent-teams' }` in the `more` group next to `subagent`.
- `AgentTeamsPage.vue`: header (title `tm('page.title')` + beta chip + refresh `v-btn`), body = flex row: `AgentTeamsSidebar` (fixed ~300px) + main area with `v-tabs` (`editor` / `monitor` / `history`) driving `v-window` panels. Tab panels host the Task 7/8/9 components behind `v-if` placeholders until those tasks land (`<section v-if="tab==='editor'">…` with a placeholder comment).
- `AgentTeamsSidebar.vue`: props `{ teams, selectedTeamId }`; emits `select(teamId)`, `create`; renders team rows (name + member count + `mdi-crown` on coordinator member name) and an "添加成员" area for the selected team listing `selectedTeam.members` with remove buttons (`emit('removeMember', memberId)`); all text via `tm()`.
- Page spec: mount with the composable mocked (`vi.mock('@/composables/useAgentTeams', ...)` returning canned refs) and Vuetify stubs (copy the stub objects from `CollabBindDialog.spec.ts:25-52`); assert team rows render, click emits `select`, tab switch swaps panel visibility.

- [ ] Steps: write spec → fail → implement route/nav/page/sidebar → pass → `pnpm build` → commit `feat(dashboard): add agent teams page shell and sidebar`.

---

### Task 6: Team creation + member dialogs

**Files:**
- Create: `dashboard/src/components/agent_teams/TeamCreateDialog.vue`
- Create: `dashboard/src/components/agent_teams/MemberAddDialog.vue`
- Create: `dashboard/src/components/agent_teams/TeamCreateDialog.spec.ts` (MemberAddDialog shares the pattern; one spec file covering both is acceptable)

**Interfaces:**
- `TeamCreateDialog`: props `{ modelValue: boolean }`, emits `update:modelValue`, `saved(team)`. Content: team name field; dynamic member rows (name, mode toggle 从人格/自定义 — persona `v-select` fed by `personaApi.listPersonas(...)` (copy the exact call shape from the chat persona picker; fallback: `v1.ts:1707` personaApi) OR `system_prompt` textarea; provider `v-select` fed by `providerApi.listByProviderType("chat_completion")` with a "默认" empty option, mirroring `Chat.vue:2481`); coordinator radio group bound to member name (default first). Submit → `useAgentTeams().createTeam({name, members, coordinator, config})` → toast + emit saved.
- `MemberAddDialog`: single-member variant of the same fields; emits `saved(member)`.
- Dialog conventions: `v-dialog > v-card`; title `text-h3 pa-4 pb-0 pl-6`; actions `variant="text"` cancel + `variant="tonal"` save; **classic `modelValue`/`update:modelValue`, NO `defineModel`**; error envelope → `toast.error(res.data.message)`.

- [ ] Steps: spec first (stub Vuetify, assert emitted payloads contain member rows + coordinator, error envelope shows toast) → implement → pass → commit `feat(dashboard): add agent team and member dialogs`.

---

### Task 7: Workflow editor (Vue Flow, edit mode)

**Files:**
- Create: `dashboard/src/utils/dagCheck.ts` + `dashboard/src/utils/dagCheck.spec.ts`
- Create: `dashboard/src/components/agent_teams/TeamsFlowCanvas.vue` (shared canvas, edit + monitor modes — monitor wiring completes in Task 8)
- Create: `dashboard/src/components/agent_teams/WorkflowEditor.vue`

**Interfaces:**
- `dagCheck.ts`: `findCycle(nodes: {id}[], edges: {from,to}[]): string[] | null` (DFS back-edge walk returning the cycle path or null); `renderableError(graph): string | null` for duplicate ids/dangling edges. Spec: acyclic/cyclic/dangling/duplicate cases.
- `TeamsFlowCanvas.vue`: props `{ nodes: FlowNode[], edges: FlowEdge[], mode: 'edit'|'monitor', nodeStates? }` where `FlowNode = { id, position: {x,y}, data: { label, memberName }, class? }`; emits `connect(params)`, `nodesChange`, `update:selectedNodeId`. Implementation:

```ts
import { VueFlow, useVueFlow } from '@vue-flow/core';
import '@vue-flow/core/dist/style.css';
import '@vue-flow/core/dist/theme-default.css';
```
Edit mode: `:nodes-draggable="true" :nodes-connectable="true" :elements-selectable="true"`, `@connect` emits a normalized `{from: source, to: target}` (ignore self-loops); node drag positions tracked via `useVueFlow().onNodesChange` → emit position updates. Monitor mode: `:nodes-draggable="false" :nodes-connectable="false"`, node `class` bound to `nodeStates[id].status` (`at-node-pending|at-node-running|…`), running pulse via scoped CSS.
- `WorkflowEditor.vue`: props `{ team, workflows }`; local state `graph` (nodes/edges arrays), `layout`, `selectedNodeId`, `workflowName`, `selectedWorkflowId`. Palette = team members; "添加节点" appends a node (auto id `n{max+1}`, staggered position). Selected-node inspector (right panel): member `v-select`, task `v-textarea` with `{{input}}` insert button, live `findCycle` warning banner. 保存 → `agentTeamsApi`-level validation errors (e.g. 成员不存在) surfaced as banner text; saves layout `{[nodeId]: {x,y}}` from current node positions.

- [ ] Steps: `dagCheck.spec.ts` → fail → `dagCheck.ts` → pass → commit util; then canvas+editor (spec: stub `VueFlow` with a passthrough div, assert connect normalization drops self-loops, inspector binds selected node, save emits payload with layout) → commit `feat(dashboard): add agent teams workflow editor`.

---

### Task 8: Run monitor — control bar, grid, agent windows, DAG progress

**Files:**
- Create: `dashboard/src/components/agent_teams/AgentWindow.vue`
- Create: `dashboard/src/components/agent_teams/RunMonitor.vue`
- Create: `dashboard/src/components/agent_teams/RunMonitor.spec.ts`

**Interfaces:**
- `AgentWindow.vue`: props `{ member: {member_id, name}, window: MemberWindowState | null, nodeStatus?: string }`. Renders a card header (member color dot via `collabMemberColor(member.name)` + `collabWithAlpha`, name, node-status chip) and a scrollable body: `sent` line styled as a quoted inbound block, then `messageBlocks({ type: 'bot', message: parts })`-grouped rendering copying `CollabTranscriptPanel.vue:86-113` — `ReasoningBlock` for `kind === 'thinking'` blocks, `MarkdownMessagePart` for plain parts with `:is-streaming="window.streaming"` and `CHAT_MARKDOWN_CUSTOM_TAGS`; fall back to `streamText` as a single plain part when `parts` is empty. Autoscroll on content change (copy `CollabTranscriptPanel`'s watch).
- `RunMonitor.vue`: props `{ team }`; consumes `useAgentTeamsRun()` + `useAgentTeams()`. Control bar: 目标 `v-textarea` (1 row, auto-grow), workflow `v-select` (options = team workflows + 无), mode `v-select` with `dag` enabled and `auto` disabled + hint `tm('monitor.autoModeDisabled')`, action buttons conditional on `runState.status` (idle→开始运行; running→暂停/停止; paused→继续/停止; paused + pausedNodeId→重试该节点/跳过并级联跳过; interrupted/interrupted-seeded→恢复运行). Grid: `GridLayout`/`GridItem` from `grid-layout-plus` over `team.members` — layout computed as `cols = Math.ceil(Math.sqrt(n))`, each tile `w = 12/cols` (min 3), `h = 6`, persisted per team in `localStorage['agent-teams-grid-' + team.team_id]`; each tile hosts an `AgentWindow` bound to `runState.windows[member.member_id]`. DAG progress strip: `v-progress-linear` computed from `runState.progress` + the Task 7 canvas in monitor mode (`nodeStates` prop) toggled by a `v-btn-toggle` (窗口 / DAG 视图). Mount effect: `loadActiveRuns()` → if a run exists for this team, `openRun(run_id)` (recovery). 409/active-conflict and other error envelopes → toast.
- `RunMonitor.spec.ts`: stub `GridLayout`/`GridItem` (render slots), stub the canvas; with `useAgentTeamsRun` mocked: assert button visibility matrix per status (idle shows 开始运行 only; paused-with-node shows 重试/跳过), assert clicking 开始运行 calls `startRun(team.team_id, {mode:'dag', input, workflow_id})`, assert grid renders one `AgentWindow` per member and forwards `window` state.

- [ ] Steps: spec → fail → implement → pass → `pnpm build` → commit `feat(dashboard): add agent teams run monitor`.

---

### Task 9: History tab + page integration

**Files:**
- Create: `dashboard/src/components/agent_teams/RunsHistory.vue`
- Modify: `dashboard/src/views/AgentTeamsPage.vue` (replace the three placeholder panels with `WorkflowEditor` / `RunMonitor` / `RunsHistory`, wire dialog mounts and cross-component events)

**Interfaces:**
- `RunsHistory.vue`: props `{ team }`; loads `agentTeamsApi.listTeamRuns(team.team_id)` on team change; table-ish `v-card` rows (time `updated_at`, mode chip, input truncated, status chip colored); row action 打开 → emits `open(runId)`; page wires that to switching to the monitor tab + `useAgentTeamsRun().openRun(runId)` (history rows carry `graph`/`node_states` so the reducer seeds correctly; the SSE stream then replays event history on top).
- Page integration: team create/delete flows through dialogs + `useAgentTeams`; selected team drives all three tabs; `TeamCreateDialog`/`MemberAddDialog` mounted at page level with `v-model` flags.

- [ ] Steps: implement RunsHistory (spec: mocked api returns rows, asserts render + open emit) → wire page → full `pnpm exec vitest run` → `pnpm build` → commit `feat(dashboard): integrate agent teams page tabs and history`.

---

### Task 10: Full verification

- [ ] **Step 1:** `cd dashboard && pnpm exec vitest run` — entire frontend suite green (pre-existing failures must be triaged: run once on the pre-Task-1 commit if any appear, same protocol as Plan 1).
- [ ] **Step 2:** `pnpm build` — production build + type-check clean.
- [ ] **Step 3:** `pnpm generate:api` idempotency — regenerated output matches the committed files (no drift).
- [ ] **Step 4:** Manual smoke (requires `uv run main.py` + `pnpm dev`, surfaced to the human if the executor cannot run a browser):
  1. `/agent-teams` creates a team (2 personas + coordinator) — three sessions appear in ChatUI.
  2. Build a 2-node workflow, save, run with a goal — windows stream member replies; DAG 视图 colors nodes; completion shows 已完成.
  3. Pause mid-run → 继续; stop mid-run → 已停止; kill backend mid-run → restart → run shows 已中断 with 恢复运行 working.
- [ ] **Step 5:** Commit any residual fixes: `git commit -m "fix(dashboard): agent teams frontend verification fixes"`.

---

## Plan Notes

- **Out of scope (Plan 3):** auto-orchestration UI enablement (the `auto` option stays disabled), stop cancel-propagation (`on_member_stop`), Collab panel removal from Chat.vue, member-direct-messaging tools.
- The backend SSE contract this frontend consumes: `data: {json}\n\n` frames with event payloads per reducer types; `: heartbeat` comment lines every 15s; full history replay on attach.
- `collabMemberColor`/`collabWithAlpha` are imported from `useAgentCollab.ts` (stays until Plan 3 removes collab; if Plan 3 lands first, move the color helpers into `agentTeams` utils first).
- If `personaApi.listPersonas`' exact signature differs from the chat picker's usage, copy the picker's call verbatim (grep `listPersonas` in `src/` for the canonical call site) — do not invent parameters.

<template>
  <section class="workflow-workspace">
    <div v-if="!conversationRunId && (mode === 'assets' || !selectedRun)" class="at-asset-toolbar">
      <h2 class="text-h5">
        {{
          tm(
            mode === "runs"
              ? "workbench.runs"
              : assetKind === "teams"
              ? "workbench.teams"
              : "workbench.workflows",
          )
        }}
      </h2>
      <span v-if="mode === 'assets'" class="at-asset-count">{{ assetCards.length }}</span>
      <v-spacer />
      <v-btn
        v-if="mode === 'assets'"
        prepend-icon="mdi-import"
        variant="text"
        @click="
          exportedPackage = null;
          packageDialog = true;
        "
        >{{ tm("package.import") }}</v-btn
      >
      <v-btn
        v-if="mode === 'assets' && assetKind !== 'workflows'"
        variant="tonal"
        color="primary"
        class="at-primary-action"
        prepend-icon="mdi-plus"
        data-testid="create-autonomous-team"
        @click="editTeam(null)"
        >{{ tm("autonomous.create") }}</v-btn
      >
      <v-btn
        v-if="mode === 'assets' && assetKind !== 'teams'"
        variant="tonal"
        color="primary"
        class="at-primary-action"
        prepend-icon="mdi-plus"
        @click="editGraph(null)"
        >{{ tm("graph.create") }}</v-btn
      >
      <v-btn
        variant="text"
        :loading="loading || !!currentOperation"
        @click="refresh()"
        >{{ tm("page.refresh") }}</v-btn
      >
    </div>
    <v-alert
      v-if="error"
      type="error"
      variant="tonal"
      class="mb-4"
      role="alert"
      >{{ error }}</v-alert
    >
    <v-progress-circular v-if="conversationRunId && !selectedRun && loading" indeterminate size="24" />
    <p v-if="!conversationRunId && (mode === 'assets' || !selectedRun)" class="at-asset-intro">
      {{
        tm(
          mode === "runs"
            ? "v2.scope"
            : assetKind === "teams"
            ? "workbench.teamIntro"
            : "workbench.workflowIntro",
        )
      }}
    </p>

    <template v-if="mode === 'assets'">
      <v-alert v-for="(reason, section) in sectionErrors" :key="section" type="warning" variant="tonal" class="mb-3">
        {{ tm('assetLoading.' + section) }} · {{ reason }}
        <v-btn variant="text" @click="loadAssetSection(section, runRead)">{{ tm("assetLoading.retry") }}</v-btn>
      </v-alert>
      <v-alert v-for="item in unavailableAssets" :key="item.asset_id" type="warning" variant="tonal" class="mb-3">
        {{ tm("assetLoading.unsupported", { id: item.asset_id, version: item.version }) }}
      </v-alert>
      <v-alert v-if="!assetCards.length && !unavailableAssets.length && !Object.keys(sectionErrors).length && !loading" type="info" variant="tonal" class="at-empty-state">{{ tm("v2.emptyAssets") }}</v-alert>
      <div class="at-asset-grid">
        <template v-for="{ asset, draft } in assetCards" :key="asset.asset_id">
          <v-card v-if="draft" class="at-asset-card at-draft-card" variant="outlined">
            <v-card-text>
              <div class="at-card-topline">
                <span class="at-asset-icon" :class="{ 'is-team': asset.kind === 'autonomous' }" aria-hidden="true"><v-icon :icon="asset.kind === 'autonomous' ? 'mdi-account-group-outline' : 'mdi-sitemap-outline'" size="23" /></span>
                <v-chip size="small" color="warning" variant="tonal">{{ tm("draft.unpublished") }}</v-chip>
              </div>
              <v-card-title>{{ asset.body.definition?.name || tm("draft.untitled") }}</v-card-title>
              <p class="at-card-description">{{ tm(asset.kind === 'autonomous' ? "workbench.teamIntro" : "workbench.workflowIntro") }}</p>
            </v-card-text>
            <v-card-actions>
              <v-btn variant="tonal" color="primary" prepend-icon="mdi-pencil-outline" @click="asset.kind === 'autonomous' ? editTeam({ asset_id: asset.asset_id, definition: asset.body.definition }) : editGraph({ asset_id: asset.asset_id, definition: asset.body.definition })">{{ tm("v2.edit") }}</v-btn>
              <v-spacer />
              <v-btn data-testid="delete-asset" variant="text" color="error" :aria-label="tm('deletion.delete')" :title="tm('deletion.delete')" class="at-card-icon-action" @click="deleteTarget = { asset_id: asset.asset_id, definition: asset.body.definition }"><v-icon icon="mdi-trash-can-outline" size="18" /><span class="at-sr-only">{{ tm('deletion.delete') }}</span></v-btn>
            </v-card-actions>
          </v-card>
          <v-card v-else class="at-asset-card" :class="{ 'at-example-card': asset.is_builtin_example }"
            :data-testid="asset.is_builtin_example ? 'builtin-workflow-example' : undefined" variant="outlined">
            <v-card-text>
              <div class="at-card-topline">
                <span class="at-asset-icon" :class="{ 'is-team': asset.definition.kind === 'autonomous' }" aria-hidden="true"><v-icon :icon="asset.definition.kind === 'autonomous' ? 'mdi-account-group-outline' : 'mdi-sitemap-outline'" size="23" /></span>
                <span v-if="asset.is_builtin_example" class="at-example-label"><v-icon icon="mdi-pin-outline" size="15" /> {{ tm("releaseBrief.pinned") }}</span>
                <v-chip size="small" variant="tonal">v{{ asset.version }}</v-chip>
              </div>
              <v-card-title>{{ assetDisplayName(asset) }}</v-card-title>
              <p v-if="asset.is_builtin_example && asset.version === 1" class="at-card-description">{{ tm("releaseBrief.description") }}</p>
              <p v-else-if="asset.summary" class="at-card-description">{{ asset.summary.preview.map((name: string, i: number) => roleNames.get(asset.summary.preview_role_ids?.[i]) ?? name).join(' · ') }}</p>
              <p v-else class="at-card-description">{{ asset.definition.kind === 'autonomous' ? asset.definition.allowed_roles.map((member: any) => member.role?.name || roles.find((role: any) => role.role_id === member.role_id)?.definition.name || member.id).filter(Boolean).join(' · ') : asset.definition.tasks.map((task: any) => task.name).slice(0, 4).join(' · ') }}</p>
              <div class="at-card-metadata">
                <span v-if="asset.definition.kind === 'autonomous'"><v-icon icon="mdi-account-multiple-outline" size="15" /> {{ tm("autonomous.allowedRoles") }} · {{ asset.summary?.count ?? asset.definition.allowed_roles.length }}</span>
                <span v-else><v-icon icon="mdi-transit-connection-variant" size="15" /> {{ tm("graph.count", { count: asset.summary?.count ?? asset.definition.tasks.length }) }} <span class="at-meta-divider">·</span> {{ tm("graph.parallel") }} {{ asset.definition.max_parallel }}</span>
                <span v-if="drafts.some((item) => item.asset_id === asset.asset_id && item.published_revision !== item.revision)" class="at-draft-indicator" data-testid="editing-draft"><i />{{ tm("draft.editing") }}</span>
              </div>
            </v-card-text>
            <v-card-actions class="at-card-footer">
              <v-btn v-if="asset.is_builtin_example" variant="text" class="at-guide-toggle"
                prepend-icon="mdi-play-box-multiple-outline" data-testid="workflow-example-guide-toggle"
                :aria-expanded="guideOpen" aria-controls="workflow-example-guide" @click="guideOpen = !guideOpen">
                {{ tm(guideOpen ? "releaseBrief.guide.close" : "releaseBrief.guide.open") }}
              </v-btn>
              <div class="at-card-secondary-actions">
                <v-btn variant="text" class="at-card-icon-action" data-testid="open-chat-access" :aria-label="tm('chatAccess.title')" :title="tm('chatAccess.title')" @click="chatAccessAsset = asset"><v-icon icon="mdi-link-variant" size="18" /><span class="at-sr-only">{{ tm("chatAccess.title") }}</span></v-btn>
                <v-btn variant="text" class="at-card-icon-action" :aria-label="tm('package.export')" :title="tm('package.export')" @click="exportWorkflow(asset)"><v-icon icon="mdi-export-variant" size="18" /><span class="at-sr-only">{{ tm("package.export") }}</span></v-btn>
                <v-btn data-testid="delete-asset" variant="text" class="at-card-icon-action" :aria-label="tm('deletion.delete')" :title="tm('deletion.delete')" @click="deleteTarget = asset"><v-icon icon="mdi-trash-can-outline" size="18" /><span class="at-sr-only">{{ tm('deletion.delete') }}</span></v-btn>
              </div>
              <v-spacer />
              <v-btn variant="text" data-testid="edit-workflow-asset" @click="asset.definition.kind === 'autonomous' ? openCard(asset, 'edit') : editGraph(asset)">{{ tm(asset.is_builtin_example ? "releaseBrief.canvas" : "v2.edit") }}</v-btn>
              <v-btn variant="tonal" color="primary" prepend-icon="mdi-play-outline" data-testid="start-workflow-asset" @click="openCard(asset, 'start')">{{ tm("v2.start") }}</v-btn>
            </v-card-actions>
            <div v-if="asset.is_builtin_example" v-show="guideOpen" id="workflow-example-guide" class="at-guide-body">
              <WorkflowExampleGuide v-if="guideOpen" />
            </div>
          </v-card>
        </template>
      </div>
    </template>

    <DeleteAssetDialog :name="deleteTarget ? assetDisplayName(deleteTarget) : null" :busy="deleting" :error="deleteError" @cancel="deleteTarget = null; deleteError = ''" @confirm="deleteAsset" />
    <template v-if="mode !== 'assets'">
      <template v-if="!conversationRunId">
      <nav
        class="at-run-filters"
        :aria-label="tm('workbench.runs')"
      >
        <v-btn
          v-for="view in ['all', 'active', 'pending', 'history'] as const"
          :key="view"
          :data-testid="`runs-${view}`"
          :variant="runView === view ? 'tonal' : 'text'"
          :aria-pressed="runView === view"
          color="primary"
          @click="runView = view"
          >{{ tm(`workbench.${view}`) }}<span v-if="view === 'pending'">
            · {{ humanRequests.length }}</span
          ></v-btn
        >
        <v-spacer />
        <v-select
          v-model="runType"
          :label="tm('trial.filter')"
          :items="runTypeOptions"
          density="compact"
          hide-details
          style="max-width: 220px; min-width: 160px"
        />
        <v-btn v-if="selectedRun" variant="text" :disabled="!!currentOperation" @click="refresh">{{ tm('page.refresh') }}</v-btn>
      </nav>
      <section
        v-if="runView === 'pending'"
        :aria-label="tm('human.inbox')"
        data-testid="human-inbox"
      >
        <h3 class="text-h6">{{ tm("human.inbox") }} · {{ visibleHumanRequests.length }}</h3>
        <div v-for="todo in visibleHumanRequests" :key="todo.todo_id">
          <v-chip size="small">{{ tm(todo.trial ? "trial.test" : "trial.production") }}</v-chip>
          <v-btn variant="text" @click="openRequest(todo)">{{ tm("human.openProcess") }}</v-btn>
          <WorkflowHumanTodo
            :todo="todo"
            :paused="todo.control_status === 'paused'"
            :cancelled="false"
            :busy="inboxBusy"
            @submit="(id, payload) => answerInbox(todo.root_run_id, id, payload)"
          />
        </div>
      </section>
      <UnifiedRunHistory
        v-if="runView === 'history'"
        :run-type="runType"
        @open-run="
          runView = 'all';
          openRun($event);
        "
      />
      <v-alert
        v-if="runView !== 'history' && !runs.length && !loading"
        type="info"
        variant="tonal"
        >{{ tm("v2.emptyRuns") }}</v-alert
      >
      <v-alert
        v-else-if="runView !== 'history' && !visibleRuns.length && !loading"
        type="info"
        variant="tonal"
        class="mb-4"
        >{{ tm("workbench.emptyFilter") }}</v-alert
      >
      <details
        v-if="runView !== 'history'"
        :open="!selectedRun"
        class="workflow-run-list mb-4"
      >
        <summary class="text-subtitle-1 mb-3">
          {{ tm("progress.runList") }} · {{ visibleRuns.length }} /
          {{ runs.length }}
        </summary>
        <v-card
          v-for="run in visibleRuns"
          :key="run.run_id"
          class="mb-3"
          variant="outlined"
        >
          <v-card-text class="d-flex flex-wrap align-center ga-2">
            <div class="flex-grow-1 workflow-name">
              <strong>{{ run.snapshot.definition.name }}</strong>
              <v-chip size="small" class="ml-2">{{
                tm(run.snapshot.trial ? "trial.test" : "trial.production")
              }}</v-chip>
              <p class="text-body-2">
                {{ tm(`v2.states.${run.status}`) }} · {{ run.run_id }}
              </p>
              <p class="text-body-2 mt-2 workflow-text">
                {{
                  run.snapshot.task ??
                  Object.values(run.snapshot.inputs ?? {}).join(" · ")
                }}
              </p>
              <p v-if="run.progress" class="text-caption mt-2">
                {{ tm("progress.title") }}: {{ run.progress.completed }} /
                {{ run.progress.total }} · {{ tm("progress.attention") }}:
                {{ run.progress.waiting }}
              </p>
              <p v-if="run.state.reason" class="workflow-text mt-2">
                {{ run.state.reason }}
              </p>
              <p v-if="run.artifact_count ?? run.artifacts?.length" class="text-caption mt-1">
                {{ tm("progress.artifacts") }}: {{ run.artifact_count ?? run.artifacts.length }}
              </p>
            </div>
            <v-btn variant="text" @click="openRun(run.run_id)">{{
              tm("v2.details")
            }}</v-btn>
          </v-card-text>
        </v-card>
      </details>
      </template>
      <v-card
        v-if="selectedRun && runView !== 'history'"
        variant="outlined"
        class="at-run-workspace"
        data-testid="run-detail"
      >
        <v-card-text class="workbench-body">
          <div class="at-run-header">
            <h2 v-if="!conversationRunId" class="text-h5 font-weight-bold flex-grow-1">{{ selectedRun.snapshot.definition.name }}</h2>
            <v-btn
              v-if="!conversationRunId && selectedRun.snapshot.root_run_id === selectedRun.run_id"
              variant="text"
              :to="{ name: 'ChatRunConversation', params: { runId: selectedRun.run_id } }"
            >{{ tm('runConversation.openChat') }}</v-btn>
            <v-chip v-if="!conversationRunId && selectedRun.snapshot.trial" size="small" color="primary" variant="outlined">{{ tm('trial.test') }} · r{{ selectedRun.snapshot.trial.draft_revision }}</v-chip>
            <v-chip v-if="!conversationRunId" variant="tonal" :color="observedStatus === 'completed' ? 'success' : 'primary'">{{ tm(`v2.states.${observedStatus}`) }}</v-chip>
            <v-btn variant="text" :aria-pressed="runDisplay === 'workbench'" @click="runDisplay = 'workbench'">{{ tm('workbench.workspace') }}</v-btn>
            <v-btn variant="text" :aria-pressed="runDisplay === 'graph'" @click="runDisplay = 'graph'">{{ tm('progress.graph') }}</v-btn>
          </div>
          <p class="workflow-text at-run-goal" data-testid="run-goal">{{ selectedRun.snapshot.task ?? (selectedRun.snapshot.definition?.port_contract_version === 2
            ? Object.keys(selectedRun.snapshot.inputs ?? {}).join(' · ')
            : Object.values(selectedRun.snapshot.inputs ?? {}).join(' · ')) }}</p>
          <p class="at-run-snapshot">
            <v-icon size="small" class="mr-1">mdi-lock-outline</v-icon>{{ tm("workbench.snapshotHint") }}
          </p>
          <nav v-show="runDisplay === 'workbench'" class="workbench-panel-nav mb-4" :aria-label="tm('workbench.panels')">
            <v-btn v-for="panel in ['tasks', 'process', 'results'] as const" :key="panel" :variant="activeRunPanel === panel ? 'tonal' : 'text'" :aria-pressed="activeRunPanel === panel" @click="activeRunPanel = panel">{{ tm(`workbench.panel${panel}`) }}</v-btn>
          </nav>
          <WorkflowGraphCanvas v-if="runDisplay === 'graph'" :definition="selectedRun.runtime_definition ?? selectedRun.snapshot.definition" :tasks="selectedRun.tasks" :child-assets="selectedRun.snapshot.child_assets ?? {}" readonly @select-task="selectRunTask" />
          <div v-show="runDisplay === 'workbench'" class="workbench-columns" :data-active-panel="activeRunPanel">
            <div class="workbench-column workbench-tasks">
              <WorkflowRunProgress
                compact
                :run="selectedRun"
                :signal="observation.signal"
                :selected-task="selectedTaskNode"
                @select="selectRunTask"
                @open-artifact="openIterationArtifact"
              />
            </div>
            <div ref="processPane" class="workbench-column workbench-process" @scroll="onProcessScroll">
              <details class="at-process-history"><summary><v-icon icon="mdi-history" size="16" />{{ tm("progress.loadOlder") }}</summary><p class="text-caption text-medium-emphasis">{{ tm("progress.historyHint") }}</p><div class="at-history-actions">
              <v-btn variant="tonal" :disabled="historyBusy || historyDone[detailRun.run_id]" @click="loadOlderProcess">{{ tm("progress.loadOlder") }}</v-btn>
              <v-btn variant="text" :disabled="historyBusy || historyNext[detailRun.run_id + ':messages'] === null" @click="loadOlderHistory('messages')">{{ tm("progress.loadMessages") }}</v-btn>
              <v-btn variant="text" :disabled="historyBusy || historyNext[detailRun.run_id + ':events'] === null" @click="loadOlderHistory('events')">{{ tm("progress.loadEvents") }}</v-btn>
              <v-btn variant="text" :disabled="historyBusy || historyNext[detailRun.run_id + ':attempts'] === null" @click="loadOlderHistory('attempts')">{{ tm("progress.loadAttempts") }}</v-btn>
              <v-btn v-if="historyReading[detailRun.run_id] || historyTruncated[detailRun.run_id] || historyNewUpdates[detailRun.run_id]" variant="text" :loading="historyRestoring[detailRun.run_id]" @click="reloadLatestProcess">{{ tm("progress.reloadLatest") }}</v-btn>
              </div></details>
              <p v-if="historyNewUpdates[detailRun.run_id]" class="text-info" role="status">{{ tm("progress.newActivity") }}</p>
              <p v-if="historyTruncated[detailRun.run_id]" class="text-warning">{{ tm("progress.historyWindow") }}</p>
              <p v-else-if="historyOlderHidden[detailRun.run_id]" class="text-caption">{{ tm("progress.olderHistoryHidden") }}</p>
              <p v-if="historyErrors[detailRun.run_id]" role="alert">{{ historyErrors[detailRun.run_id] }}</p>
              <p
                v-if="conversationRunId && detailRun.tool_calls?.some((call: any) => ['started', 'unknown'].includes(call.status))"
                class="text-caption mb-2"
              >{{ tm("runConversation.reconcileInWorkbench") }}</p>
              <WorkflowRunProcess :key="detailRun.run_id" :run="detailRun" :readonly="!!conversationRunId" :signal="observation.signal" :busy="!!currentOperation" @resolve="resolveTool" />
            </div>
            <div class="workbench-column workbench-results">
          <section
            v-if="selectedRun.todos?.length"
            :aria-label="tm('human.title')"
            class="at-human-section"
          >
            <h3 class="text-h6">{{ tm("human.title") }}</h3>
            <p class="text-body-2">{{ tm("human.localWait") }}</p>
            <WorkflowHumanTodo
              v-for="todo in selectedRun.todos"
              :key="todo.todo_id"
              :todo="todo"
              :paused="selectedRun.control?.status === 'paused'"
              :cancelled="selectedRun.control?.status === 'cancelled'"
              :busy="!!currentOperation"
              @submit="answerTodo"
            />
          </section>
          <WorkflowRunControls
            :key="selectedRun.run_id"
            :run="selectedRun"
            :task="detailRun"
            :busy="!!currentOperation"
            :feedback="visibleControlFeedback"
            @submit="applyRunControl"
          />
          <details class="my-3 text-caption">
            <summary>{{ tm('workbench.identifiers') }}</summary>
            <p class="workflow-text mt-2">{{ tm('v2.attempt') }}: {{ detailRun.state.attempt_id }}</p>
            <p class="workflow-text">{{ tm('v2.member') }}: {{ detailRun.snapshot.member?.member_id ?? '—' }}</p>
            <p class="workflow-text">{{ tm('child.root') }}: {{ selectedRun.run_id }}</p>
          </details>
          <h3 class="at-artifacts-heading">{{ tm("progress.artifacts") }}</h3>
          <v-btn
            v-if="
              ['workflow_call_v2', 'team_call_v2'].includes(detailRun.snapshot.engine) &&
              detailRun.state.child_scope_run_id
            "
            variant="tonal"
            data-testid="open-child-workflow"
            @click="
              selectRunTask(
                selectedRun.scopes?.find(
                  (scope: any) =>
                    scope.run_id === detailRun.state.child_scope_run_id,
                )?.tasks?.[0]?.run_id ?? null,
              )
            "
            >{{ tm("workflowCall.open") }}</v-btn
          >
          <p
            v-if="detailRun.state.child_result_ref"
            class="text-caption my-2"
            data-testid="child-return-reference"
          >
            {{ tm("workflowCall.returnTask") }}:
            {{ detailRun.state.child_result_ref.artifact_id }} · v{{
              detailRun.state.child_result_ref.version
            }}
          </p>
          <template v-if="detailRun.snapshot.coordinator">
            <v-btn
              v-for="call in allRunTasks.filter(
                (task: any) =>
                  task.snapshot.workflow &&
                  task.snapshot.created_by_task_id === detailRun.state.task_id,
              )"
              :key="call.run_id"
              variant="tonal"
              class="mr-2 mb-2"
              data-testid="coordinator-workflow-call"
              @click="selectRunTask(call.run_id)"
              >{{ tm("workflowCall.open") }}:
              {{ call.snapshot.definition.name }}</v-btn
            >
          </template>
          <v-select
            v-if="selectedRun.tasks?.length"
            v-model="selectedTaskNode"
            :items="
              allRunTasks.map((task: any) => ({
                title: `${task.snapshot.definition.name}${
                  taskScopeNames[task.run_id]
                    ? ` · ${tm('child.scope')}: ${taskScopeNames[task.run_id]}`
                    : ''
                }${
                  task.snapshot.iteration
                    ? ` · ${tm('loops.iteration')} ${task.snapshot.iteration}`
                    : ''
                }`,
                value: task.run_id,
              }))
            "
            :label="tm('graph.selectTask')"
            class="mt-3"
          />
          <v-alert
            v-if="
              detailRun.state.resolution_pending &&
              selectedRun.control?.status !== 'cancelled'
            "
            type="info"
            variant="tonal"
            class="my-3"
            data-testid="resolution-saved"
            >{{ tm("resolution.savedWaiting") }}</v-alert
          >
          <v-alert
            v-if="showRecovery"
            type="info"
            variant="tonal"
            class="my-3"
            data-testid="recovery-info"
          >
            <p>{{ tm("recovery.sameAttempt") }}</p>
            <RunnerCapabilities
              :runner="detailRun.snapshot.effective?.runner"
              :identity="detailRun.recovery.remote_identity"
            />
            <p class="mt-2">
              {{
                tm(
                  `recovery.boundaries.${
                    detailRun.recovery.boundary ?? "none"
                  }`,
                )
              }}
            </p>
            <p v-if="detailRun.recovery.reason" class="mt-2">
              {{ detailRun.recovery.reason }}
            </p>
            <p
              v-if="!detailRun.recovery.remote_identity"
              class="text-body-2 mt-2"
            >
              {{ tm("recovery.limits") }}
            </p>
            <v-btn
              v-if="!conversationRunId && detailRun.recovery.available"
              variant="tonal"
              class="mt-3"
              :loading="
                currentOperation?.kind === 'recover' &&
                currentOperation.taskId === detailRun.run_id
              "
              :disabled="
                !!currentOperation ||
                selectedRun.control?.status === 'paused' ||
                selectedRun.control?.status === 'cancelled'
              "
              data-testid="recover-attempt"
              @click="recoverAttempt"
              >{{
                tm(
                  detailRun.recovery.capability === "remote_query"
                    ? "runner.query"
                    : "recovery.continue",
                )
              }}</v-btn
            >
          </v-alert>
          <details v-if="!showRecovery" class="my-3">
            <summary>{{ tm('workbench.executionDetails') }}</summary>
          <RunnerCapabilities
            :runner="detailRun.snapshot.effective?.runner"
            :identity="detailRun.recovery?.remote_identity"
            class="my-3"
          />
          </details>
          <v-alert
            v-if="detailRun.state.reason"
            type="warning"
            variant="tonal"
            class="my-3"
            >{{ detailRun.state.reason }}</v-alert
          >
          <details v-if="detailRun.run_id !== selectedRun.run_id" class="my-3">
            <summary>{{ tm("usage.title") }}</summary>
            <RunUsagePanel v-if="detailRun.usage" :key="detailRun.run_id" :run="detailRun" :signal="observation.signal" />
            <p v-else-if="conversationRunId">{{ tm("usage.unknown") }}</p>
          </details>
          <p class="text-body-2 text-medium-emphasis mb-3">
            {{ detailRun.snapshot.definition.name }} · {{ tm("v2.result") }}
          </p>
          <v-alert
            v-if="detailRun.validation_errors?.length"
            type="error"
            variant="tonal"
            class="my-3"
          >
            <p v-for="field in detailRun.validation_errors" :key="field.path">
              {{ field.path }}: {{ field.message }}
            </p>
          </v-alert>
          <v-select
            v-if="detailRun.artifacts?.length"
            v-model="artifactVersion"
            :items="artifactVersions"
            :label="tm('v2.artifactVersion')"
            data-testid="artifact-version"
            @update:model-value="historicalArtifact = null; loadArtifact()"
          />
          <p v-if="fileError" role="alert" class="text-error">{{ fileError }}</p>
          <pre
            v-if="
              selectedArtifact || (!detailRun.result_ref && detailRun.result)
            "
            class="workflow-text workflow-result mt-2"
            data-testid="result-text"
            >{{ resultText }}</pre
          >
          <p v-else>{{ tm("v2.noResult") }}</p>
          <div
            v-for="file in selectedArtifact?.files ??
            selectedArtifact?.content.files ??
            []"
            :key="file.file_id"
            class="d-flex flex-wrap ga-2 my-2"
          >
            <span class="workflow-name">{{
              file.filename ?? file.file_id
            }}</span>
            <v-btn variant="text" @click="readFile(file.file_id, false)">{{
              tm("v2.previewFile")
            }}</v-btn>
            <v-btn variant="tonal" @click="readFile(file.file_id, true)">{{
              tm("v2.downloadFile")
            }}</v-btn>
          </div>
          <v-btn
            v-if="!conversationRunId && selectedArtifact"
            variant="tonal"
            class="mt-2"
            @click="prepareRevision"
            >{{ tm("v2.revise") }}</v-btn
          >
          <details
            v-if="detailRun.snapshot.input_artifacts?.length"
            class="workflow-text mt-3"
          >
            <summary>{{ tm("v2.pinnedInputs") }}</summary>
            <p v-for="ref in detailRun.snapshot.input_artifacts" :key="`${ref.artifact_id}:${ref.version}`" class="text-caption mt-2">{{ ref.artifact_id }} · v{{ ref.version }}</p>
          </details>
          <div v-if="boundSources.length" class="mt-4" data-testid="bound-port-sources">
            <h4 class="text-subtitle-2">{{ tm("v2.boundPortSources") }}</h4>
            <div v-for="source in boundSources" :key="source.key" class="d-flex flex-wrap align-center ga-2 my-2">
              <span class="text-caption">
                {{ source.label }} · {{ source.ref.artifact_id }} · v{{ source.ref.version }}
                <template v-if="source.ref.file_id"> · {{ source.ref.file_id }}</template>
              </span>
              <template v-if="source.ref.file_id">
                <v-btn variant="text" size="small" @click="readFile(source.ref.file_id, false, source)">
                  {{ tm("v2.previewFile") }}
                </v-btn>
                <v-btn variant="tonal" size="small" @click="readFile(source.ref.file_id, true, source)">
                  {{ tm("v2.downloadFile") }}
                </v-btn>
              </template>
            </div>
          </div>
          <v-expansion-panels class="mt-4" variant="accordion">
            <v-expansion-panel :title="tm('v2.contract')">
              <v-expansion-panel-text>
                <pre class="workflow-text">{{
                  JSON.stringify(
                    detailRun.snapshot.definition.output_contract ?? {
                      type: "object",
                      properties: { text: { type: "string", minLength: 1 } },
                      required: ["text"],
                      additionalProperties: false,
                    },
                    null,
                    2,
                  )
                }}</pre>
              </v-expansion-panel-text>
            </v-expansion-panel>
            <v-expansion-panel :title="tm('v2.submissions')">
              <v-expansion-panel-text>
                <pre class="workflow-text">{{
                  JSON.stringify(
                    detailRun.events?.filter((event: any) =>
                      event.kind?.startsWith("result."),
                    ),
                    null,
                    2,
                  )
                }}</pre>
              </v-expansion-panel-text>
            </v-expansion-panel>
            <v-expansion-panel :title="tm('v2.effective')">
              <v-expansion-panel-text>
                <pre class="workflow-text">{{
                  JSON.stringify(detailRun.snapshot.effective, null, 2)
                }}</pre>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
            </div>
          </div>
        </v-card-text>
      </v-card>
    </template>

    <AutonomousTeamDialog
      v-model="teamEditing"
      :asset="editingAsset"
      :roles="roles"
      :catalog="catalog"
      @saved="refresh()"
      @started="trialStarted"
    />
    <v-dialog
      class="workbench-dialog"
      scrollable
      v-model="starting"
      max-width="600"
      :persistent="saving"
    >
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("v2.start")
        }}</v-card-title>
        <v-card-text v-if="runAsset">
          <p v-if="checkingReadiness" role="status">
            {{ tm("runner.checking") }}
          </p>
          <v-alert
            v-if="readiness && !readiness.ready"
            type="warning"
            variant="tonal"
            data-testid="runner-readiness-blocked"
          >
            <p v-for="issue in readiness.issues" :key="issue.path">
              {{ issue.path }} · {{ issue.message }}
            </p>
          </v-alert>
          <details v-if="readiness?.roles?.length" class="mb-4">
            <summary>{{ tm('workbench.executionDetails') }}</summary>
          <RunnerCapabilities
            v-for="role in readiness?.roles ?? []"
            :key="role.role_id + ':' + role.version"
            :runner="role.runner"
            class="mb-3"
          />
          </details>
          <p class="mb-4">
            {{ assetDisplayName(runAsset) }} · v{{ runAsset.version }}
          </p>
          <StructuredWorkflowInputs
            v-if="runAsset.definition.input_contract"
            v-model="structuredInputs"
            :contract="runAsset.definition.input_contract"
            :history="availableInputs"
            :key="runAsset.asset_id"
          />
          <v-text-field
            v-for="name in runAsset.definition.required_inputs"
            :key="name"
            v-model="inputs[name]"
            :label="name"
            :rules="[(value) => !!value?.trim() || tm('v2.required')]"
          />
          <v-select
            v-if="runAsset.definition.kind === 'autonomous'"
            v-model="inputArtifacts"
            :items="availableInputs"
            multiple
            chips
            :label="tm('v2.pinnedInputs')"
          />
          <v-alert v-if="formError" type="error" variant="tonal">{{
            formError
          }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="saving" @click="starting = false">{{
            tm("v2.close")
          }}</v-btn>
          <v-btn
            variant="text"
            :disabled="saving || checkingReadiness"
            @click="checkReadiness"
            >{{ tm("runner.check") }}</v-btn
          >
          <v-btn
            variant="tonal"
            color="primary"
            :loading="saving"
            :disabled="!hasPendingLaunch && (checkingReadiness || !readiness?.ready)"
            @click="start"
            >{{ tm("v2.start") }}</v-btn
          >
        </v-card-actions>
      </v-card>
    </v-dialog>
    <v-dialog
      class="workbench-dialog"
      scrollable
      v-if="!conversationRunId"
      v-model="revising"
      max-width="720"
      :persistent="saving"
    >
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("v2.revise")
        }}</v-card-title>
        <v-card-text>
          <p class="mb-3">{{ tm("v2.revisionHint") }}</p>
          <v-textarea
            v-model="revisionText"
            :label="tm('v2.resultJson')"
            rows="10"
          />
          <v-file-input
            v-if="selectedRun?.snapshot.definition.output_contract"
            v-model="revisionFiles"
            multiple
            :label="tm('v2.attachFile')"
          />
          <v-alert v-if="formError" type="error" variant="tonal">{{
            formError
          }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="saving" @click="revising = false">{{
            tm("v2.close")
          }}</v-btn>
          <v-btn variant="tonal" :loading="saving" @click="saveRevision">{{
            tm("v2.saveRevision")
          }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
    <v-dialog
      class="workbench-dialog"
      scrollable
      v-model="previewing"
      max-width="900"
      @update:model-value="!$event && clearFilePreview()"
    >
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("v2.previewFile")
        }}</v-card-title>
        <v-card-text>
          <pre v-if="previewText" class="workflow-text">{{ previewText }}</pre>
          <ImagePreview v-else-if="previewBlob?.type.startsWith('image/')" :blob="previewBlob" :filename="previewFilename" />
          <PdfPreview v-else-if="previewBlob?.type === 'application/pdf'" :blob="previewBlob" />
          <iframe
            v-else-if="previewUrl"
            :src="previewUrl"
            sandbox=""
            :title="tm('v2.previewFile')"
            style="width: 100%; height: 60vh; border: 0"
          />
        </v-card-text>
        <v-card-actions
          ><v-spacer /><v-btn variant="text" @click="clearFilePreview">{{
            tm("v2.close")
          }}</v-btn></v-card-actions
        >
      </v-card>
    </v-dialog>
    <WorkflowPackageDialog
      v-if="!conversationRunId"
      v-model="packageDialog"
      :exported-package="exportedPackage"
      @installed="
        refresh();
        emit('installed', $event);
      "
    />
    <WorkflowChatAccessDialog
      v-if="chatAccessAsset"
      :key="`${chatAccessAsset.asset_id}:${chatAccessAsset.version}`"
      :asset="chatAccessAsset"
      :model-value="true"
      @update:model-value="chatAccessAsset = null"
    />
  </section>
</template>

<script setup lang="ts">
import UnifiedRunHistory from "./UnifiedRunHistory.vue";
import ImagePreview from "@/components/chat/message_list_comps/binary_preview/ImagePreview.vue";
import PdfPreview from "@/components/chat/message_list_comps/binary_preview/PdfPreview.vue";
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { isAxiosError } from "axios";
import { serialize, sha256 } from "ohash";
import { accountSessionRevision } from "@/utils/accountSession";
import { roleAssetsApi } from "@/api/roleAssets";
import { draftApi } from "@/composables/useAssetDraft";
import { workflowAssetsApi } from "@/api/workflowAssets";
import StructuredWorkflowInputs, { parseWorkflowInputs } from "./StructuredWorkflowInputs.vue";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";
import WorkflowExampleGuide from "./WorkflowExampleGuide.vue";
import WorkflowPackageDialog from "./WorkflowPackageDialog.vue";
import WorkflowChatAccessDialog from "./WorkflowChatAccessDialog.vue";
import RunLimitsForm from "./RunLimitsForm.vue";
import RunUsagePanel from "./RunUsagePanel.vue";
import AutonomousTeamDialog from "./AutonomousTeamDialog.vue";
import WorkflowRunProgress from "./WorkflowRunProgress.vue";
import WorkflowRunProcess from "./WorkflowRunProcess.vue";
import WorkflowRunControls from "./WorkflowRunControls.vue";
import WorkflowHumanTodo from "./WorkflowHumanTodo.vue";
import RunnerCapabilities from "./RunnerCapabilities.vue";
import DeleteAssetDialog from "./DeleteAssetDialog.vue";
import type {
  ToolResolutionRequest,
  WorkflowControlRequest,
  WorkflowRerunRequest,
  WorkflowRetryRequest,
  HumanAnswerRequest,
  WorkflowPackage,
} from "@/api/generated/openapi-v1";

const props = defineProps<
  | { mode: "assets"; assetKind?: "teams" | "workflows"; conversationRunId?: never }
  | { mode: "runs"; conversationRunId?: string; assetKind?: never }
>();
const emit = defineEmits<{
  (event: "started", runId: string): void;
  (event: "installed", directory: "teams" | "workflows"): void;
  (event: "run-status", status: string): void;
  (event: "run-unavailable"): void;
}>();
const { tm } = useModuleI18n("features/agent-teams");
const route = useRoute();
const router = useRouter();
const assets = ref<any[]>([]);
const deleteTarget = ref<any>(null);
const deleting = ref(false);
const deleteError = ref("");
function assetDisplayName(asset: { definition: { name?: string }; is_builtin_example?: boolean }) {
  return asset.is_builtin_example && asset.definition.name === "Release brief"
    ? tm("releaseBrief.title")
    : asset.definition.name || tm("draft.untitled");
}
async function deleteAsset() {
  if (!deleteTarget.value || deleting.value) return;
  const assetId = deleteTarget.value.asset_id;
  deleting.value = true;
  deleteError.value = "";
  try {
    await workflowAssetsApi.delete(assetId);
    if (disposed) return;
    assets.value = assets.value.filter((asset) => asset.asset_id !== assetId);
    drafts.value = drafts.value.filter((draft) => draft.asset_id !== assetId);
    deleteTarget.value = null;
    // A list requested before deletion can still contain the removed version.
    if (refreshingRead !== null) {
      runRead++;
      loading.value = false;
    }
    await refresh();
  } catch (cause) {
    deleteError.value = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    deleting.value = false;
  }
}
const drafts = ref<any[]>([]);
async function trialStarted(runId: string) {
  await router.replace({ query: { ...route.query, workflow_run: runId } });
  emit("started", runId);
}
const visibleDrafts = computed(() =>
  drafts.value.filter(
    (item) =>
      !assets.value.some((asset) => asset.asset_id === item.asset_id) &&
      (props.assetKind === "teams"
        ? item.kind === "autonomous"
        : props.assetKind === "workflows"
        ? item.kind === "workflow"
        : true),
  ),
);
const visibleAssets = computed(() =>
  assets.value.filter(
    (asset) =>
      !props.assetKind ||
      (asset.definition.kind === "autonomous") ===
        (props.assetKind === "teams"),
  ),
);
const assetCards = computed(() => [
  ...visibleDrafts.value.map((asset) => ({ asset, draft: true })),
  ...visibleAssets.value.map((asset) => ({ asset, draft: false })),
].sort((a, b) => Number(b.asset.is_builtin_example === true) - Number(a.asset.is_builtin_example === true)));
const guideOpen = ref(false);
const chatAccessAsset = ref<any>(null);
const packageDialog = ref(false);
const exportedPackage = ref<WorkflowPackage | null>(null);

async function exportWorkflow(asset: any) {
  try {
    exportedPackage.value = await workflowAssetsApi.exportPackage(
      asset.asset_id,
      asset.version,
    );
    packageDialog.value = true;
  } catch (cause) {
    error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  }
}
const roles = ref<any[]>([]);
const roleNames = computed(() => new Map(roles.value.map((role) => [role.role_id, role.definition?.name])));
const catalog = ref<Record<string, any[]>>({
  profiles: [],
  models: [],
  personas: [],
  tools: [],
  skills: [],
  knowledge_bases: [],
});
const runs = ref<any[]>([]);
const humanRequests = ref<any[]>([]);
const inboxBusy = ref(false);
async function openRequest(todo: any) {
  await openRun(todo.root_run_id);
  if (selectedRun.value?.run_id !== todo.root_run_id) return;
  runView.value = "all";
  const source = allRunTasks.value.find((task) =>
    todo.recommendation_ref &&
    task.result_ref?.artifact_id === todo.recommendation_ref.artifact_id &&
    task.result_ref?.version === todo.recommendation_ref.version,
  );
  selectRunTask(source?.run_id ?? todo.task_run_id);
}
async function answerInbox(
  rootId: string,
  todoId: string,
  payload: HumanAnswerRequest,
) {
  const signal = observation.value.signal;
  if (inboxBusy.value || signal.aborted) return;
  inboxBusy.value = true;
  try {
    let updated;
    try {
      updated = await workflowAssetsApi.answer(rootId, todoId, payload);
    } catch (err) {
      if (!isAxiosError(err) || err.response?.status !== 409) throw err;
      if (signal.aborted || openingRunId || runView.value !== "pending") return;
      if (selectedRun.value?.run_id === rootId) {
        updated = await workflowAssetsApi.run(rootId, signal);
      } else {
        await openRun(rootId);
      }
    }
    if (disposed) return;
    if (updated && !signal.aborted && !openingRunId && selectedRun.value?.run_id === rootId) {
      applyOperationSnapshot(updated);
    }
    const inbox = await workflowAssetsApi.humanRequests();
    if (!disposed) humanRequests.value = inbox.requests;
  } catch (err) {
    if (!signal.aborted) {
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
    }
  } finally {
    inboxBusy.value = false;
  }
}

const runView = ref<"all" | "active" | "pending" | "history">("all");
const runType = ref<"all" | "test" | "production">("all");
const runTypeOptions = computed(() =>
  (["all", "test", "production"] as const).map((value) => ({
    title: tm(`trial.${value}`), value,
  })),
);
const visibleHumanRequests = computed(() =>
  humanRequests.value.filter((todo) => {
    if (runType.value === "all") return true;
    return (runType.value === "test") === !!todo.trial;
  }),
);
const visibleRuns = computed(() =>
  runs.value.filter((run) => {
    if (runType.value !== "all" && (runType.value === "test") !== !!run.snapshot.trial)
      return false;
    const terminal = ["completed", "cancelled", "skipped"].includes(run.status);
    if (runView.value === "history")
      return terminal || ["failed", "interrupted"].includes(run.status);
    if (runView.value === "active") return !terminal;
    if (runView.value === "pending")
      return (
        !terminal &&
        ([
          "blocked",
          "failed",
          "interrupted",
          "paused",
          "waiting_human",
        ].includes(run.status) ||
          run.control?.status === "paused" ||
          run.progress?.waiting > 0 ||
          run.todos?.some((todo: any) => todo.status === "pending"))
      );
    return true;
  }),
);
const observation = shallowRef(new AbortController());
const selectedRun = ref<any>(null);
const controlFeedback = ref<{
  runId: string;
  message: string;
  error: boolean;
  revision?: number;
}>();
const visibleControlFeedback = computed(() => {
  const feedback = controlFeedback.value;
  if (!feedback || feedback.runId !== selectedRun.value?.run_id) return;
  if (feedback.revision !== undefined && feedback.revision !== selectedRun.value.control?.revision) return;
  return feedback;
});
const observedStatus = computed(() => {
  const run = selectedRun.value;
  if (!run) return "";
  if (run.control?.status === "paused" && !["completed", "cancelled", "failed", "skipped"].includes(run.status)) {
    return "paused";
  }
  if (run.status === "running" && run.progress?.running === 0 &&
      run.todos?.some((todo: { status: string }) => todo.status === "pending")) {
    return "waiting_human";
  }
  return run.status;
});
watch(observedStatus, (status) => {
  if (props.conversationRunId && status) emit("run-status", status);
});
const runDisplay = ref<'workbench' | 'graph'>('workbench');
const activeRunPanel = ref<'tasks' | 'process' | 'results'>('process');
const runOperations = ref(
  new Map<
    string,
    { kind: "recover" | "control" | "resolve" | "answer"; taskId: string }
  >(),
);
const currentOperation = computed(() =>
  selectedRun.value
    ? runOperations.value.get(selectedRun.value.run_id)
    : undefined,
);
const selectedTaskNode = ref<string | null>(null);
const processPane = ref<HTMLElement | null>(null);
const followProcess = ref(true);
function onProcessScroll() {
  const pane = processPane.value;
  const taskId = detailRun.value?.run_id;
  if (!pane || !taskId) return;
  if (pane.scrollHeight - pane.scrollTop - pane.clientHeight >= 48) {
    followProcess.value = false;
    historyReading.value[taskId] = true;
  } else if (!historyPinned.value[taskId] && !historyTruncated.value[taskId] && !historyReturning.value[taskId]) {
    delete historyReading.value[taskId];
    delete historyNewUpdates.value[taskId];
    followProcess.value = true;
  }
}
watch(() => selectedRun.value?.cursor, async () => {
  if (!followProcess.value) return;
  await nextTick();
  if (processPane.value) processPane.value.scrollTop = processPane.value.scrollHeight;
});
const allRunTasks = computed<any[]>(() => [
  ...new Map(
    [
      ...(props.conversationRunId && selectedRun.value ? [selectedRun.value] : []),
      ...(selectedRun.value?.tasks ?? []),
      ...(selectedRun.value?.scopes ?? []).flatMap((scope: any) => scope.tasks),
      ...[selectedRun.value, ...(selectedRun.value?.scopes ?? [])].flatMap((scope: any) =>
        (scope?.loops ?? []).flatMap((loop: any) =>
          loop.iterations.flatMap((iteration: any) => iteration.tasks),
        ),
      ),
    ].map((task: any) => [task.run_id, task]),
  ).values(),
]);
const taskScopeNames = computed<Record<string, string>>(() => Object.fromEntries(
  (selectedRun.value?.scopes ?? []).flatMap((scope: any) => [
    ...(scope.tasks ?? []),
    ...(scope.loops ?? []).flatMap((loop: any) => loop.iterations.flatMap((iteration: any) => iteration.tasks)),
  ].map((task: any) => [task.run_id, scope.snapshot?.definition?.name ?? scope.run_id])),
));
const detailRun = computed(
  () =>
    allRunTasks.value.find(
      (task: any) =>
        task.run_id === selectedTaskNode.value,
    ) ??
    selectedRun.value?.tasks?.[0] ??
    selectedRun.value,
);
const showRecovery = computed(() => {
  const task = detailRun.value;
  return task?.recovery && (
    ["interrupted", "failed", "blocked"].includes(task.status) ||
    (task.status === "awaiting_result" && task.recovery.has_saved_tool_results)
  );
});
function runViews(root: any): any[] {
  if (!root) return [];
  return [root, ...(root.tasks ?? []),
    ...(root.scopes ?? []).flatMap((scope: any) => [scope, ...scope.tasks]),
    ...[root, ...(root.scopes ?? [])].flatMap((scope: any) =>
      (scope.loops ?? []).flatMap((loop: any) =>
        loop.iterations.flatMap((iteration: any) => iteration.tasks)))];
}
function messageBelongs(root: any, target: any, message: any): boolean {
  return target.run_id === root.run_id ||
    (target.snapshot?.engine === "graph_v2" && target.state?.scope_id === message.scope_id) ||
    target.run_id === message.sender_task_run_id ||
    (target.state?.scope_id === message.scope_id && target.snapshot?.member?.member_id === message.recipient_member_id);
}
const historyNext = ref<Record<string, string | null>>({});
const historyDone = ref<Record<string, boolean>>({});
const historyBusy = ref(false);
const historyErrors = ref<Record<string, string>>({});
const historyTruncated = ref<Record<string, boolean>>({});
const historyReading = ref<Record<string, boolean>>({});
const historyPinned = ref<Record<string, boolean>>({});
const historyNewUpdates = ref<Record<string, boolean>>({});
const historyOlderHidden = ref<Record<string, boolean>>({});
const historyRestoring = ref<Record<string, boolean>>({});
const historyReturning = ref<Record<string, boolean>>({});
const historyPending = ref<Record<string, Partial<Record<"process" | "events" | "messages" | "attempts", any[]>>>>({});
watch([() => selectedRun.value?.run_id, () => observation.value], () => {
  historyBusy.value = false;
  historyDone.value = {};
  historyNext.value = {};
  historyErrors.value = {};
  historyTruncated.value = {};
  historyReading.value = {};
  historyPinned.value = {};
  historyNewUpdates.value = {};
  historyOlderHidden.value = {};
  historyRestoring.value = {};
  historyReturning.value = {};
  historyPending.value = {};
});
watch(() => detailRun.value?.run_id, (taskId, previousId) => {
  if (previousId && previousId !== taskId) {
    delete historyReading.value[previousId];
    delete historyPinned.value[previousId];
    if (historyTruncated.value[previousId]) void restoreLatest(previousId);
    else delete historyNewUpdates.value[previousId];
  }
  followProcess.value = true;
  if (taskId && taskId !== previousId && historyTruncated.value[taskId]) {
    delete historyReading.value[taskId];
    delete historyPinned.value[taskId];
    void restoreLatest(taskId);
  } else if (taskId && taskId !== previousId) {
    void nextTick().then(() => {
      if (detailRun.value?.run_id === taskId && followProcess.value && processPane.value)
        processPane.value.scrollTop = processPane.value.scrollHeight;
    });
  }
});

function mergeHistoryWindow(target: any, kind: "process" | "events" | "messages" | "attempts", incoming: any[], older = false) {
  const key = kind === "process" ? "record_id" : kind === "events" ? "cursor" : kind === "messages" ? "message_id" : "attempt_id";
  const existing = target[kind] ?? [];
  const known = new Set(existing.map((entry: any) => entry[key]));
  if (!older && historyReturning.value[target.run_id]) {
    const pending = historyPending.value[target.run_id] ??= {};
    const entries = new Map([...(pending[kind] ?? []), ...incoming].map((entry: any) => [entry[key], entry]));
    pending[kind] = [...entries.values()].sort((a: any, b: any) => (a.cursor ?? 0) - (b.cursor ?? 0)).slice(-512);
  }
  const isReading = target.run_id === detailRun.value?.run_id && !!(historyReading.value[target.run_id] || historyReturning.value[target.run_id]);
  if (!older && isReading && incoming.some((entry: any) => !known.has(entry[key])))
    historyNewUpdates.value[target.run_id] = true;
  const entries = new Map([...existing, ...incoming].map((entry: any) => [entry[key], entry]));
  const ordered = [...entries.values()].sort((a: any, b: any) => (a.cursor ?? 0) - (b.cursor ?? 0));
  if (ordered.length <= 512) {
    target[kind] = ordered;
    return;
  }
  if (older || isReading) {
    target[kind] = ordered.slice(0, 512);
    historyTruncated.value[target.run_id] = true;
  } else {
    target[kind] = ordered.slice(-512);
    historyOlderHidden.value[target.run_id] = true;
    if (kind === "process") delete historyDone.value[target.run_id];
    else {
      delete historyNext.value[`${target.run_id}:${kind}`];
      const floor = `${kind === "events" ? "event" : kind === "messages" ? "message" : "attempt"}_history_before`;
      target[floor] = Math.max(Number(target[floor] ?? 1), target[kind][0].cursor ?? 1);
    }
  }
}

async function loadOlderProcess() {
  if (openingRunId || historyBusy.value || !selectedRun.value || !detailRun.value) return;
  const signal = observation.value.signal;
  if (signal.aborted) return;
  const rootId = selectedRun.value.run_id;
  const taskId = detailRun.value.run_id;
  const cursors = (detailRun.value.process ?? []).map((record: any) => record.cursor).filter((cursor: any) => Number.isInteger(cursor));
  const before = cursors.length ? Math.min(...cursors) : selectedRun.value.cursor + 1;
  historyBusy.value = true;
  historyReading.value[taskId] = true;
  historyPinned.value[taskId] = true;
  followProcess.value = false;
  delete historyErrors.value[taskId];
  try {
    const page = await workflowAssetsApi.processPage(rootId, taskId, before, signal);
    if (signal.aborted || selectedRun.value?.run_id !== rootId || detailRun.value?.run_id !== taskId || !historyReading.value[taskId]) return;
    const pane = processPane.value;
    const height = pane?.scrollHeight ?? 0;
    const top = pane?.scrollTop ?? 0;
    const targets = runViews(selectedRun.value);
    for (const target of targets.filter((run: any) => run.run_id === taskId)) {
      mergeHistoryWindow(target, "process", page.records, true);
    }
    historyDone.value = { ...historyDone.value, [taskId]: page.next_before === null };
    await nextTick();
    if (!signal.aborted && pane && detailRun.value?.run_id === taskId) pane.scrollTop = top + pane.scrollHeight - height;
  } catch (cause) {
    if (!signal.aborted && detailRun.value?.run_id === taskId)
      historyErrors.value[taskId] = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    if (!signal.aborted) historyBusy.value = false;
  }
}
async function loadOlderHistory(kind: "events" | "messages" | "attempts") {
  if (openingRunId || historyBusy.value || !selectedRun.value || !detailRun.value) return;
  const signal = observation.value.signal;
  if (signal.aborted) return;
  const rootId = selectedRun.value.run_id;
  const taskId = detailRun.value.run_id;
  const key = `${taskId}:${kind}`;
  const next = historyNext.value[key];
  if (next === null) return;
  const floor = detailRun.value[kind === "events" ? "event_history_before" : kind === "messages" ? "message_history_before" : "attempt_history_before"] ?? 1;
  const applied = selectedRun.value.cursor;
  const before = next ?? String(Number.isInteger(applied) ? Math.min(floor, applied + 1) : floor);
  historyBusy.value = true;
  historyReading.value[taskId] = true;
  historyPinned.value[taskId] = true;
  followProcess.value = false;
  delete historyErrors.value[taskId];
  try {
    const page = await workflowAssetsApi.runHistoryPage(rootId, taskId, kind, before, signal);
    if (signal.aborted || selectedRun.value?.run_id !== rootId || detailRun.value?.run_id !== taskId || !historyReading.value[taskId]) return;
    const pane = processPane.value;
    const height = pane?.scrollHeight ?? 0;
    const top = pane?.scrollTop ?? 0;
    for (const target of runViews(selectedRun.value).filter((view: any) => view.run_id === taskId)) {
      mergeHistoryWindow(target, kind, page.items, true);
    }
    historyNext.value[key] = page.next_before;
    await nextTick();
    if (!signal.aborted && pane && detailRun.value?.run_id === taskId) pane.scrollTop = top + pane.scrollHeight - height;
  } catch (cause) {
    if (!signal.aborted && detailRun.value?.run_id === taskId)
      historyErrors.value[taskId] = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    if (!signal.aborted) historyBusy.value = false;
  }
}
async function restoreLatest(taskId: string) {
  const root = selectedRun.value;
  const signal = observation.value.signal;
  if (!root || signal.aborted || historyRestoring.value[taskId]) return;
  const rootId = root.run_id;
  const boundary = root.cursor;
  historyRestoring.value[taskId] = true;
  delete historyErrors.value[taskId];
  try {
    const [process, events, messages, attempts] = await Promise.all([
      workflowAssetsApi.processPage(rootId, taskId, boundary + 1, signal),
      workflowAssetsApi.runHistoryPage(rootId, taskId, "events", String(boundary + 1), signal),
      workflowAssetsApi.runHistoryPage(rootId, taskId, "messages", String(boundary + 1), signal),
      workflowAssetsApi.runHistoryPage(rootId, taskId, "attempts", String(boundary + 1), signal),
    ]);
    if (signal.aborted || selectedRun.value?.run_id !== rootId || historyReading.value[taskId]) return;
    const pages = { process: process.records, events: events.items, messages: messages.items, attempts: attempts.items };
    for (const target of runViews(selectedRun.value).filter((view: any) => view.run_id === taskId)) {
      for (const kind of ["process", "events", "messages", "attempts"] as const) {
        const key = kind === "process" ? "record_id" : kind === "events" ? "cursor" : kind === "messages" ? "message_id" : "attempt_id";
        const later = (target[kind] ?? []).filter((entry: any) => entry.cursor > boundary);
        const pending = (historyPending.value[taskId]?.[kind] ?? []).filter((entry: any) => entry.cursor > boundary);
        const entries = new Map([...pages[kind], ...later, ...pending].map((entry: any) => [entry[key], entry]));
        const ordered = [...entries.values()].sort((a: any, b: any) => (a.cursor ?? 0) - (b.cursor ?? 0));
        const trimmed = ordered.length > 512;
        target[kind] = ordered.slice(-512);
        if (kind === "process") historyDone.value[taskId] = !trimmed && process.next_before === null;
        else {
          const page = kind === "events" ? events : kind === "messages" ? messages : attempts;
          historyNext.value[`${taskId}:${kind}`] = trimmed ? String(target[kind][0].cursor) : page.next_before;
        }
        if (trimmed || (kind === "process" ? process.next_before : (kind === "events" ? events : kind === "messages" ? messages : attempts).next_before))
          historyOlderHidden.value[taskId] = true;
      }
    }
    delete historyTruncated.value[taskId];
    delete historyPinned.value[taskId];
    delete historyNewUpdates.value[taskId];
    delete historyReturning.value[taskId];
    if (detailRun.value?.run_id === taskId) {
      followProcess.value = true;
      await nextTick();
      if (!signal.aborted && detailRun.value?.run_id === taskId && followProcess.value && processPane.value)
        processPane.value.scrollTop = processPane.value.scrollHeight;
    }
  } catch (cause) {
    if (!signal.aborted && selectedRun.value?.run_id === rootId) {
      historyErrors.value[taskId] = extractApiError(cause, tm("errors.operationFailed")).message;
      if (detailRun.value?.run_id === taskId) {
        historyReading.value[taskId] = true;
        followProcess.value = false;
      }
    }
  } finally {
    if (!signal.aborted) {
      delete historyRestoring.value[taskId];
      delete historyPending.value[taskId];
    }
  }
}
async function reloadLatestProcess() {
  const signal = observation.value.signal;
  const taskId = detailRun.value?.run_id;
  if (signal.aborted || !taskId || historyBusy.value || historyRestoring.value[taskId]) return;
  delete historyReading.value[taskId];
  historyReturning.value[taskId] = true;
  followProcess.value = false;
  try {
    await restoreLatest(taskId);
  } finally {
    if (!signal.aborted) delete historyReturning.value[taskId];
  }
}
const teamEditing = ref(false);
const selectedArtifact = ref<any>(null);
const historicalArtifact = ref<{ taskId: string; ref: PortArtifactReference } | null>(null);
const historicalResult = computed(() =>
  historicalArtifact.value?.taskId === detailRun.value?.run_id ? historicalArtifact.value?.ref : undefined,
);
const artifactVersions = computed(() =>
  (detailRun.value?.artifacts ?? []).map(
    (item: { version: number }) => item.version,
  ),
);
const resultText = computed(() => {
  const content = selectedArtifact.value?.content ?? detailRun.value?.result;
  return content &&
    Object.keys(content).length === 1 &&
    typeof content.text === "string"
    ? content.text
    : JSON.stringify(content, null, 2);
});
type PortArtifactReference = { artifact_id: string; version: number; file_id?: string };
type BoundPortSource = { artifact?: PortArtifactReference; reference?: PortArtifactReference };
type DisplayedPortSource = { key: string; label: string; ref: PortArtifactReference };
const boundSources = computed<DisplayedPortSource[]>(() => {
  const run = detailRun.value;
  if (!run) return [];
  return [run, ...(run.tasks ?? [])].flatMap((task: any) =>
    Object.entries<BoundPortSource>(task.snapshot?.port_sources ?? {}).flatMap(([port, source]) => {
      const ref = source.reference ?? source.artifact;
      return ref ? [{
        key: `${task.run_id}:${port}`,
        label: `${task.snapshot?.definition?.name ?? task.node_id} · ${port}`,
        ref,
      }] : [];
    }),
  );
});
const artifactVersion = ref<number>(1);
const inputArtifacts = ref<string[]>([]);
const revising = ref(false);
const revisionText = ref("");
const revisionFiles = ref<File[]>([]);
const revisionRequestId = ref("");
const fileError = ref("");
let previewRead = 0;
let boundPreview = false;
const previewing = ref(false);
const previewText = ref("");
const previewUrl = ref("");
const previewBlob = ref<Blob | null>(null);
const previewFilename = ref("");
function clearFilePreview() {
  previewRead++;
  boundPreview = false;
  previewing.value = false;
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
  previewUrl.value = "";
  previewText.value = "";
  previewBlob.value = null;
}
const inputSourceRuns = ref<any[]>([]);
const availableInputs = computed(() =>
  inputSourceRuns.value.flatMap((run) =>
    (run.artifacts ?? []).map((artifact: any) => ({
      title: `${run.snapshot.definition.name} · ${artifact.artifact_id} v${artifact.version}`,
      value: JSON.stringify({
        artifact_id: artifact.artifact_id,
        version: artifact.version,
      }),
    })),
  ),
);
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const formError = ref("");
const starting = ref(false);
const editingAsset = ref<any>(null);
const runAsset = ref<any>(null);
const inputs = ref<Record<string, string>>({});
const structuredInputs = ref("{}");
let timer: ReturnType<typeof setInterval> | undefined;
let disposed = false;
watch(accountSessionRevision, () => {
  disposed = true;
  observation.value.abort();
  assets.value = [];
  inputSourceRuns.value = [];
  unavailableAssets.value = [];
  sectionErrors.value = {};
  drafts.value = [];
  guideOpen.value = false;
  deleteTarget.value = null;
  starting.value = false;
  chatAccessAsset.value = null;
  packageDialog.value = false;
  selectedRun.value = null;
  selectedArtifact.value = null;
  clearFilePreview();
  if (timer) clearInterval(timer);
}, { flush: "sync" });
let artifactRead = 0;
let pendingArtifact: { key: string; signal: AbortSignal; result: Promise<boolean> } | undefined;
let runRead = 0;
let refreshingRead: number | null = null;
let openingRunId: string | undefined;

const assetSections = ["assets", "roles", "runs", "resources", "workflowDrafts", "teamDrafts"] as const;
type AssetSection = typeof assetSections[number];
const sectionErrors = ref<Partial<Record<AssetSection, string>>>({});
const unavailableAssets = ref<{asset_id: string; version: number}[]>([]);
const sectionRequests = new Map<AssetSection, number>();

async function loadAssetSection(section: AssetSection, read: number) {
  const request = (sectionRequests.get(section) ?? 0) + 1;
  sectionRequests.set(section, request);
  const current = () => !disposed && read === runRead && sectionRequests.get(section) === request;
  try {
    if (section === "assets") {
      const data = await workflowAssetsApi.list(props.assetKind === "workflows", true);
      if (!current()) return;
      assets.value = data.assets.map((row: any) => row.summary ? { ...row, definition: row.summary } : row);
      unavailableAssets.value = data.unavailable_assets ?? [];
    } else if (section === "roles") {
      const data = await roleAssetsApi.list();
      if (current()) roles.value = data.roles;
    } else if (section === "runs") {
      const data = await workflowAssetsApi.runs();
      if (current()) runs.value = data.runs;
    } else if (section === "resources") {
      const data = await roleAssetsApi.resources();
      if (current() && data) catalog.value = data;
    } else {
      const kind = section === "workflowDrafts" ? "workflow" : "autonomous";
      const items = await draftApi.list(kind);
      if (current()) drafts.value = [...drafts.value.filter((item) => item.kind !== kind), ...items.map((item) => ({ ...item, kind }))];
    }
    if (current()) delete sectionErrors.value[section];
  } catch (cause) {
    if (current()) sectionErrors.value[section] = extractApiError(cause, tm("errors.operationFailed")).message;
  }
}

let openingCard = 0;
async function openCard(asset: any, action: "edit" | "start") {
  const request = ++openingCard;
  try {
    const detail = asset.summary ? await workflowAssetsApi.get(asset.asset_id, asset.version) : asset;
    if (disposed || request !== openingCard || !assets.value.some((item) => item.asset_id === asset.asset_id)) return;
    if (detail.deleted) { await refresh(); return; }
    if (action === "edit") editTeam(detail);
    else prepareRun(detail);
  } catch (cause) {
    if (!disposed && request === openingCard) error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  }
}

/**
 * Refresh visible data while keeping timer-driven observations quiet.
 *
 * Args:
 *   background: Avoid foreground loading state and skip polling in history.
 *
 * Returns:
 *   Resolves when the current observation completes or becomes obsolete.
 */
async function refresh(background = false) {
  if (
    observation.value.signal.aborted ||
    loading.value ||
    refreshingRead === runRead ||
    currentOperation.value ||
    openingRunId ||
    (background && runView.value === "history")
  )
    return;
  const signal = observation.value.signal;
  const read = ++runRead;
  refreshingRead = read;
  if (!background) loading.value = true;
  try {
    if (props.mode === "assets") {
      await Promise.all(assetSections.map((section) => loadAssetSection(section, read)));
    } else {
      if (!props.conversationRunId) {
        const [runData, inboxData] = await Promise.all([
          workflowAssetsApi.runs(),
          workflowAssetsApi.humanRequests(),
        ]);
        if (signal.aborted || read !== runRead || (background && runView.value === "history")) return;
        runs.value = runData.runs;
        humanRequests.value = inboxData.requests;
      }
      const id = props.conversationRunId ?? route.query.workflow_run ?? selectedRun.value?.run_id;
      if (typeof id === "string" && id) {
        const previousResult = detailRun.value?.result_ref;
        if (
          selectedRun.value?.run_id === id &&
          selectedRun.value.cursor !== undefined
        ) {
          const changes = await workflowAssetsApi.events(
            id,
            selectedRun.value.cursor,
            signal,
          );
          if (signal.aborted || read !== runRead || (background && runView.value === "history")) return;
          let snapshotAhead = false;
          if (!changes.snapshot && changes.events?.some((event: any) =>
            typeof event.kind === "string" && !event.kind.startsWith("process.") &&
            !event.kind.startsWith("checkpoint."),
          )) {
            const state = await workflowAssetsApi.run(id, signal);
            if (signal.aborted || read !== runRead || (background && runView.value === "history")) return;
            // A newer state can update status, but its tail must wait for replay.
            snapshotAhead = state.cursor > changes.cursor;
            changes.snapshot = { ...state, cursor: changes.cursor };
          }
          if (changes.snapshot) {
            if (changes.reset || changes.cursor > selectedRun.value.cursor) {
              const previous = selectedRun.value;
              const snapshot = changes.snapshot;
              if (changes.cursor >= previous.cursor) {
                const oldViews = runViews(previous);
                const newViews = runViews(snapshot);
                for (const target of newViews) {
                  const old = oldViews.find((view: any) => view.run_id === target.run_id);
                  for (const kind of ["process", "events", "messages", "attempts"] as const) {
                    const incoming = (target[kind] ?? []).filter(
                      (entry: any) => !snapshotAhead || !Number.isInteger(entry.cursor) || entry.cursor <= changes.cursor,
                    );
                    if (kind === "process") incoming.push(...(changes.records ?? []).filter((record: any) => record.run_id === target.run_id));
                    else if (kind === "events") incoming.push(...(changes.events ?? []).filter((event: any) => target.run_id === snapshot.run_id || target.run_id === event.run_id));
                    else if (kind === "messages") incoming.push(...(changes.messages ?? []).filter((message: any) => messageBelongs(snapshot, target, message)));
                    target[kind] = old?.[kind] ?? [];
                    mergeHistoryWindow(target, kind, incoming);
                  }
                }
              } else {
                historyDone.value = {};
                historyNext.value = {};
                historyTruncated.value = {};
                historyReading.value = {};
                historyPinned.value = {};
                historyNewUpdates.value = {};
                historyOlderHidden.value = {};
              }
              selectedRun.value = snapshot;
            }
          } else {
            const root = selectedRun.value;
            const runs = runViews(root);
            for (const target of runs) {
              mergeHistoryWindow(target, "process", (changes.records ?? []).filter((record: any) => record.run_id === target.run_id));
              mergeHistoryWindow(target, "events", (changes.events ?? []).filter((event: any) => target.run_id === root.run_id || target.run_id === event.run_id));
              mergeHistoryWindow(target, "messages", (changes.messages ?? []).filter((message: any) => messageBelongs(root, target, message)));
            }
            root.cursor = changes.cursor;
          }
        } else {
          const snapshot = await workflowAssetsApi.run(id, signal);
          if (
            disposed ||
            read !== runRead ||
            (background && runView.value === "history")
          )
            return;
          selectedRun.value = snapshot;
          selectedTaskNode.value = snapshot.tasks?.[0]?.run_id ?? null;
        }
        const result = historicalResult.value ?? detailRun.value?.result_ref;
        if (!historicalResult.value && (previousResult?.artifact_id !== result?.artifact_id ||
            (previousResult?.version !== result?.version && artifactVersion.value === previousResult?.version))) {
          artifactVersion.value = result?.version ?? 1;
        }
        if (result && (selectedArtifact.value?.artifact_id !== result.artifact_id ||
            selectedArtifact.value?.version !== artifactVersion.value)) {
          if (!(await loadArtifact())) return;
        }
      }
    }
    if (!disposed && read === runRead) error.value = "";
  } catch (err) {
    if (!signal.aborted && read === runRead && !(background && runView.value === "history")) {
      if (props.conversationRunId && isAxiosError(err) && [401, 403, 404].includes(err.response?.status ?? 0)) {
        observation.value.abort();
        emit("run-unavailable");
      } else {
        error.value = extractApiError(err, tm("errors.operationFailed")).message;
      }
    }
  } finally {
    if (read === runRead) loading.value = false;
    if (refreshingRead === read) refreshingRead = null;
  }
}

function editGraph(asset: any) {
  openingCard++;
  void router.push({ name: 'AgentWorkflowEditor', params: { assetId: asset?.asset_id ?? 'new' } });
}

watch(teamEditing, (open, previous) => { if (!open && previous) void refresh(); });

function editTeam(asset: any) {
  openingCard++;
  editingAsset.value = asset;
  teamEditing.value = true;
}

function selectRunTask(nodeId: string | null) {
  historicalArtifact.value = null;
  runDisplay.value = 'workbench';
  activeRunPanel.value = 'process';
  if (nodeId)
    selectedTaskNode.value =
      allRunTasks.value.find((task) => task.run_id === nodeId)?.run_id ??
      selectedRun.value?.tasks?.find((task: any) => task.node_id === nodeId)
        ?.run_id ??
      nodeId;
}
function openIterationArtifact(taskId: string, ref: PortArtifactReference) {
  selectRunTask(taskId);
  historicalArtifact.value = { taskId, ref };
  activeRunPanel.value = "results";
  artifactVersion.value = ref.version;
  void loadArtifact();
}
watch(
  () => detailRun.value?.run_id,
  async () => {
    clearFilePreview();
    artifactVersion.value = historicalResult.value?.version ?? detailRun.value?.result_ref?.version ?? 1;
    await loadArtifact();
  },
);

const readiness = ref<any>(null);
const checkingReadiness = ref(false);
let readinessRevision = 0;

async function checkReadiness() {
  if (!runAsset.value) return false;
  const asset = runAsset.value;
  const revision = ++readinessRevision;
  checkingReadiness.value = true;
  readiness.value = null;
  try {
    const result = await workflowAssetsApi.readiness(
      asset.asset_id,
      asset.version,
    );
    if (
      revision !== readinessRevision ||
      !starting.value ||
      runAsset.value !== asset
    )
      return false;
    readiness.value = result;
    return result.ready;
  } catch (error) {
    if (revision === readinessRevision)
      formError.value = extractApiError(
        error,
        tm("errors.operationFailed"),
      ).message;
    return false;
  } finally {
    if (revision === readinessRevision) checkingReadiness.value = false;
  }
}

const hasPendingLaunch = ref(false);
let launchStorageKey = "";

function refreshPendingLaunch() {
  const keys = Array.from({ length: sessionStorage.length }, (_, index) => sessionStorage.key(index));
  hasPendingLaunch.value = keys.some((key) => key?.startsWith(launchStorageKey));
}

function prepareRun(asset: any) {
  const account = localStorage.getItem("account_id") || localStorage.getItem("resource_owner");
  launchStorageKey = `astrbot:workflow-start:${JSON.stringify([account, asset.asset_id, asset.version])}:`;
  refreshPendingLaunch();
  runAsset.value = asset;
  structuredInputs.value = "{}";
  inputs.value = Object.fromEntries(
    (asset.definition.required_inputs ?? []).map((name: string) => [name, ""]),
  );
  formError.value = "";
  starting.value = true;
  inputArtifacts.value = [];
  void checkReadiness();
  inputSourceRuns.value = runs.value;
  const target = asset.asset_id;
  const request = openingCard;
  void workflowAssetsApi.runs(true).then((data) => {
    if (!disposed && starting.value && request === openingCard && runAsset.value?.asset_id === target)
      inputSourceRuns.value = data.runs;
  }).catch((cause) => {
    if (!disposed && starting.value && request === openingCard && runAsset.value?.asset_id === target)
      formError.value = extractApiError(cause, tm("errors.operationFailed")).message;
  });
}

async function start() {
  if (saving.value || !runAsset.value) return;
  saving.value = true;
  formError.value = "";
  const assetId = runAsset.value.asset_id;
  const storageKey = launchStorageKey;
  const launchRoute = route.fullPath;
  const account = localStorage.getItem("account_id") || localStorage.getItem("resource_owner");
  const isCurrent = () =>
    !disposed && starting.value && storageKey === launchStorageKey &&
    route.fullPath === launchRoute &&
    account === (localStorage.getItem("account_id") || localStorage.getItem("resource_owner"));
  let pendingKey: string | undefined;
  let requestId: string | null = null;

  try {
    const payload = {
      version: runAsset.value.version,
      inputs: runAsset.value.definition.input_contract
        ? parseWorkflowInputs(structuredInputs.value) : inputs.value,
      source: "dashboard",
      ...(inputArtifacts.value.length
        ? {
            input_artifacts: inputArtifacts.value.map((value) =>
              JSON.parse(value),
            ),
          }
        : {}),
    };
    const properties: Record<string, { default?: unknown }> =
      runAsset.value.definition.input_contract?.properties ?? {};
    const effectiveInputs = {
      ...Object.fromEntries(Object.entries(properties)
        .filter(([, declared]) => "default" in declared)
        .map(([name, declared]) => [name, declared.default])),
      ...payload.inputs,
    };
    const fingerprint = sha256(serialize({ ...payload, inputs: effectiveInputs }));
    pendingKey = storageKey + fingerprint;
    requestId = sessionStorage.getItem(pendingKey);
    let run;
    if (requestId) {
      try {
        run = await workflowAssetsApi.start(assetId, {
          ...payload,
          request_id: requestId,
          replay_only: true,
        });
      } catch (err) {
        const { fields } = extractApiError(err, tm("errors.operationFailed"));
        if (fields.some((field) => field.code === "LAUNCH_CONFLICT")) {
          requestId = null;
        } else if (!fields.some((field) => field.code === "LAUNCH_NOT_COMMITTED")) {
          throw err;
        }
      }
    }
    if (!run) {
      if (!isCurrent() || !(await checkReadiness()) || !isCurrent()) return;
      requestId ??= crypto.randomUUID?.() ?? crypto.getRandomValues(new Uint32Array(4)).join("-");
      sessionStorage.setItem(pendingKey, requestId);
      hasPendingLaunch.value = true;
      run = await workflowAssetsApi.start(assetId, { ...payload, request_id: requestId });
    }
    if (!isCurrent()) return;
    const navigationFailure = await router.replace({
      query: { ...route.query, workflow_run: run.run_id },
    });
    if (!navigationFailure && sessionStorage.getItem(pendingKey) === requestId) {
      sessionStorage.removeItem(pendingKey);
      refreshPendingLaunch();
    }
    if (!disposed && router.currentRoute.value.query.workflow_run === run.run_id) {
      starting.value = false;
      emit("started", run.run_id);
    }
  } catch (err) {
    if (!isCurrent()) return;
    const failure = extractApiError(err, tm("errors.operationFailed"));
    const status = (err as { response?: { status?: number } } | null)?.response?.status;
    if (
      (status === 400 || status === 422) &&
      pendingKey && sessionStorage.getItem(pendingKey) === requestId
    ) {
      sessionStorage.removeItem(pendingKey);
      refreshPendingLaunch();
    }
    formError.value = `${failure.message} ${failure.fields
      .map((field) => `${field.path}: ${field.message}`)
      .join("; ")}`;
  } finally {
    saving.value = false;
  }
}

function applyOperationSnapshot(next: any) {
  const previous = selectedRun.value;
  const snapshot = { ...next };
  if (previous && previous.run_id === snapshot.run_id && snapshot.cursor < previous.cursor) return;
  if (observation.value.signal.aborted || (props.conversationRunId && previous?.run_id !== snapshot.run_id)) return;
  // An earlier poll can no longer update state. Its events will be replayed.
  runRead++;
  loading.value = false;
  if (previous?.run_id === snapshot.run_id && Number.isInteger(previous.cursor) && snapshot.cursor >= previous.cursor) {
    const oldViews = runViews(previous);
    for (const target of runViews(snapshot)) {
      const old = oldViews.find((view: any) => view.run_id === target.run_id);
      for (const kind of ["process", "events", "messages", "attempts"] as const) {
        const incoming = (target[kind] ?? []).filter(
          (entry: any) => !Number.isInteger(entry.cursor) || entry.cursor <= previous.cursor,
        );
        target[kind] = old?.[kind] ?? [];
        mergeHistoryWindow(target, kind, incoming);
      }
    }
    // A newer state snapshot does not acknowledge events that have not been read.
    snapshot.cursor = previous.cursor;
  } else {
    historyDone.value = {};
    historyNext.value = {};
    historyTruncated.value = {};
    historyReading.value = {};
    historyPinned.value = {};
    historyNewUpdates.value = {};
    historyOlderHidden.value = {};
  }
  selectedRun.value = snapshot;
}

async function openRun(runId: string) {
  observation.value.abort();
  observation.value = new AbortController();
  const signal = observation.value.signal;
  historyBusy.value = false;
  selectedArtifact.value = null;
  clearFilePreview();
  const read = ++runRead;
  openingRunId = runId;
  loading.value = true;
  try {
    error.value = "";
    const snapshot = await workflowAssetsApi.run(runId, signal);
    if (disposed || read !== runRead) return;
    selectedRun.value = snapshot;
    historyDone.value = {};
    historyNext.value = {};
    historyErrors.value = {};
    historyTruncated.value = {};
    historyReading.value = {};
    historyPinned.value = {};
    historyNewUpdates.value = {};
    historyOlderHidden.value = {};
    followProcess.value = true;
    selectedTaskNode.value = snapshot.tasks?.[0]?.run_id ?? null;
    await router.replace({ query: { ...route.query, workflow_run: runId } });
    if (disposed || read !== runRead) return;
    artifactVersion.value = detailRun.value.result_ref?.version ?? 1;
    await loadArtifact();
  } catch (err) {
    if (!disposed && read === runRead)
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    if (read === runRead) {
      openingRunId = undefined;
      loading.value = false;
    }
  }
}

watch(() => route.query.workflow_run, (runId) => {
  if (
    props.mode === "runs" && !props.conversationRunId && typeof runId === "string" &&
    runId && runId !== (openingRunId ?? selectedRun.value?.run_id)
  ) {
    void openRun(runId);
  }
}, { flush: "sync" });

async function recoverAttempt() {
  if (
    currentOperation.value ||
    openingRunId ||
    !detailRun.value?.recovery?.available
  )
    return;
  const taskId = detailRun.value.run_id;
  const rootId = selectedRun.value.run_id;
  const read = ++runRead;
  runOperations.value.set(rootId, { kind: "recover", taskId });
  loading.value = true;
  try {
    error.value = "";
    const recovered = await workflowAssetsApi.recover(taskId);
    if (disposed || read !== runRead) return;
    const snapshot =
      rootId === taskId ? recovered : await workflowAssetsApi.run(rootId, observation.value.signal);
    if (!disposed && read === runRead) applyOperationSnapshot(snapshot);
  } catch (err) {
    if (!disposed && read === runRead)
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    if (read === runRead) loading.value = false;
    runOperations.value.delete(rootId);
  }
}

async function resolveTool(recordId: string, payload: ToolResolutionRequest) {
  const taskId = detailRun.value?.run_id;
  if (!taskId || openingRunId || currentOperation.value) return;
  const rootId = selectedRun.value.run_id;
  const read = ++runRead;
  runOperations.value.set(rootId, { kind: "resolve", taskId });
  loading.value = true;
  try {
    error.value = "";
    const resolved = await workflowAssetsApi.resolveTool(
      taskId,
      recordId,
      payload,
    );
    if (disposed || read !== runRead) return;
    const snapshot =
      rootId === taskId ? resolved : await workflowAssetsApi.run(rootId, observation.value.signal);
    if (!disposed && read === runRead) applyOperationSnapshot(snapshot);
  } catch (err) {
    if (!disposed && read === runRead)
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    if (read === runRead) loading.value = false;
    runOperations.value.delete(rootId);
  }
}

async function answerTodo(todoId: string, payload: HumanAnswerRequest) {
  const rootId = selectedRun.value?.run_id;
  if (!rootId || openingRunId || currentOperation.value) return;
  const read = ++runRead;
  runOperations.value.set(rootId, { kind: "answer", taskId: todoId });
  loading.value = true;
  try {
    error.value = "";
    const updated = await workflowAssetsApi.answer(rootId, todoId, payload);
    if (!disposed && read === runRead) applyOperationSnapshot(updated);
  } catch (err) {
    if (disposed || openingRunId || selectedRun.value?.run_id !== rootId) return;
    error.value = extractApiError(err, tm("errors.operationFailed")).message;
    if (isAxiosError(err) && err.response?.status === 409) {
      const signal = observation.value.signal;
      const conflictRead = runRead;
      try {
        const latest = await workflowAssetsApi.run(rootId, signal);
        if (!signal.aborted && conflictRead === runRead) applyOperationSnapshot(latest);
      } catch (reloadError) {
        if (
          props.conversationRunId && !signal.aborted && conflictRead === runRead &&
          isAxiosError(reloadError) && [401, 403, 404].includes(reloadError.response?.status ?? 0)
        ) {
          observation.value.abort();
          emit("run-unavailable");
        }
      }
    }
  } finally {
    if (read === runRead) loading.value = false;
    runOperations.value.delete(rootId);
  }
}

async function applyRunControl(
  payload: WorkflowControlRequest | WorkflowRetryRequest | WorkflowRerunRequest,
) {
  const rootId = selectedRun.value?.run_id;
  const signal = observation.value.signal;
  if (!rootId || signal.aborted || openingRunId || currentOperation.value) return;
  const taskId = detailRun.value.run_id;
  const retry = "attempt_id" in payload;
  const rerun = !retry && !("action" in payload);
  const read = ++runRead;
  runOperations.value.set(rootId, { kind: "control", taskId });
  controlFeedback.value = undefined;
  loading.value = true;
  let requestStorageKey: string | undefined;
  let rerunRequestId: string | undefined;
  let committed = false;
  try {
    if (rerun) {
      const account = localStorage.getItem("account_id") ?? localStorage.getItem("resource_owner");
      requestStorageKey = `astrbot:workflow-rerun:${JSON.stringify([account, rootId])}`;
      rerunRequestId = localStorage.getItem(requestStorageKey) ?? payload.request_id;
      localStorage.setItem(requestStorageKey, rerunRequestId);
      const started = await workflowAssetsApi.rerun(rootId, { request_id: rerunRequestId });
      if (signal.aborted || read !== runRead) return;
      const navigationFailure = await router.push({
        name: "ChatRunConversation",
        params: { runId: started.run_id },
      });
      if (!navigationFailure && localStorage.getItem(requestStorageKey) === rerunRequestId) {
        localStorage.removeItem(requestStorageKey);
      }
      return;
    }
    const updated = retry
      ? await workflowAssetsApi.retry(taskId, payload)
      : await workflowAssetsApi.control(rootId, payload);
    if (signal.aborted || read !== runRead) return;
    committed = true;
    controlFeedback.value = {
      runId: rootId,
      message: tm(`controls.applied.${retry ? "retry" : payload.action}`),
      error: false,
      revision: retry ? undefined : (payload.expected_revision ?? 0) + 1,
    };
    const snapshot = retry && rootId !== taskId
      ? await workflowAssetsApi.run(rootId, signal)
      : updated;
    if (!signal.aborted && read === runRead) applyOperationSnapshot(snapshot);
  } catch (err) {
    if (signal.aborted || read !== runRead) return;
    if (committed) {
      error.value = tm("controls.refreshAfterOperation");
      return;
    }
    // Only request validation guarantees rejection before any launch was committed.
    if (
      requestStorageKey && isAxiosError(err) && err.response?.status === 422 &&
      localStorage.getItem(requestStorageKey) === rerunRequestId
    ) {
      localStorage.removeItem(requestStorageKey);
    }
    controlFeedback.value = {
      runId: rootId,
      message: extractApiError(err, tm("errors.operationFailed")).message,
      error: true,
    };
  } finally {
    if (read === runRead) loading.value = false;
    runOperations.value.delete(rootId);
  }
}

/**
 * Read the selected artifact without discarding a newer version selection.
 *
 * Returns:
 *   Whether the artifact read succeeded or no artifact was required.
 */
async function loadArtifact() {
  const signal = observation.value.signal;
  if (signal.aborted) return false;
  const ref = historicalResult.value ?? detailRun.value?.result_ref;
  const version = Number(artifactVersion.value);
  if (ref && selectedArtifact.value?.artifact_id === ref.artifact_id && selectedArtifact.value?.version === version) return true;
  const key = JSON.stringify([ref?.artifact_id, version]);
  if (pendingArtifact?.key === key && pendingArtifact.signal === signal) return pendingArtifact.result;

  const read = ++artifactRead;
  fileError.value = "";
  if (!boundPreview) clearFilePreview();
  selectedArtifact.value = null;
  if (!ref) {
    pendingArtifact = undefined;
    return true;
  }
  const result = Promise.resolve()
    .then(() => workflowAssetsApi.artifact(ref.artifact_id, version, signal))
    .then((artifact) => {
      if (signal.aborted || read !== artifactRead || runView.value === "history") return false;
      selectedArtifact.value = artifact;
      return true;
    })
    .catch((err) => {
      if (!signal.aborted && read === artifactRead && runView.value !== "history")
        error.value = extractApiError(err, tm("errors.operationFailed")).message;
      return false;
    })
    .finally(() => {
      if (pendingArtifact?.result === result) pendingArtifact = undefined;
    });
  pendingArtifact = { key, signal, result };
  return result;
}

function prepareRevision() {
  revisionText.value = JSON.stringify(selectedArtifact.value.content, null, 2);
  revisionFiles.value = [];
  revisionRequestId.value = crypto.randomUUID();
  formError.value = "";
  revising.value = true;
}

async function saveRevision() {
  if (saving.value || !selectedArtifact.value) return;
  saving.value = true;
  try {
    const content = JSON.parse(revisionText.value);
    for (const file of revisionFiles.value) {
      const stored = await workflowAssetsApi.storeFile(
        detailRun.value.run_id,
        file,
      );
      content.files = [...(content.files ?? []), { file_id: stored.file_id }];
    }
    revisionText.value = JSON.stringify(content, null, 2);
    revisionFiles.value = [];
    const ref = await workflowAssetsApi.revise(
      selectedArtifact.value.artifact_id,
      {
        expected_version: Number(artifactVersion.value),
        request_id: revisionRequestId.value,
        content,
      },
    );
    artifactVersion.value = ref.version;
    revising.value = false;
    await loadArtifact();
    await refresh();
  } catch (err) {
    const failure = extractApiError(err, tm("errors.operationFailed"));
    formError.value = `${failure.message} ${failure.fields
      .map((field) => `${field.path}: ${field.message}`)
      .join("; ")}`;
  } finally {
    saving.value = false;
  }
}

async function readFile(
  fileId: string, download: boolean, source?: DisplayedPortSource,
) {
  const signal = observation.value.signal;
  const artifact = source?.ref ?? selectedArtifact.value;
  const read = artifactRead;
  const observedRun = detailRun.value?.run_id;
  if (signal.aborted || !artifact || openingRunId) return;
  const preview = download ? null : ++previewRead;
  if (!download) boundPreview = !!source;
  const isCurrent = () => !signal.aborted && (preview === null || preview === previewRead) &&
    (source ? detailRun.value?.run_id === observedRun && boundSources.value.some(
      (item) => item.key === source.key && item.ref.artifact_id === artifact.artifact_id &&
        item.ref.version === artifact.version && item.ref.file_id === fileId,
    ) : read === artifactRead);
  fileError.value = "";
  try {
    const response = await workflowAssetsApi.file(
      artifact.artifact_id,
      artifact.version,
      fileId,
      download,
      signal,
    );
    if (!isCurrent()) return;
    const disposition = response.headers?.["content-disposition"] ?? "";
    const encodedFilename = disposition.split("filename*=UTF-8''")[1];
    const filename = encodedFilename ? decodeURIComponent(encodedFilename) :
      (artifact.files ?? artifact.content?.files ?? []).find((file: any) => file.file_id === fileId)?.filename ?? fileId;
    if (download) {
      const url = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.download = filename;
      link.href = url;
      link.click();
      URL.revokeObjectURL(url);
    } else {
      const text = response.data.type.startsWith("text/") ? await response.data.text() : "";
      if (!isCurrent()) return;
      clearFilePreview();
      boundPreview = !!source;
      previewText.value = text;
      previewBlob.value = response.data.type.startsWith("image/") || response.data.type === "application/pdf"
        ? response.data : null;
      previewFilename.value = filename;
      previewUrl.value = previewText.value || previewBlob.value
        ? ""
        : URL.createObjectURL(response.data);
      previewing.value = true;
    }
  } catch (err) {
    const data = (err as { response?: { data?: unknown } })?.response?.data;
    let error = err;
    if (data instanceof Blob && data.type.includes("json")) {
      try {
        error = { response: { data: JSON.parse(await data.text()) } };
      } catch {
        // Keep the original error if the response could not be decoded.
      }
    }
    if (isCurrent())
      fileError.value = extractApiError(error, tm("errors.operationFailed")).message;
  }
}

onMounted(async () => {
  await refresh();
  // Poll persisted progress. Unmount only closes observation, never the run.
  if (!disposed && props.mode === "runs")
    timer = setInterval(() => void refresh(true), 2000);
});
onUnmounted(() => {
  disposed = true;
  if (timer) clearInterval(timer);
  observation.value.abort();
  clearFilePreview();
});
</script>

<style scoped>
.workflow-workspace { min-width: 0; container-type: inline-size; }
.at-asset-count { display: grid; place-items: center; min-width: 24px; height: 24px; padding: 0 7px; border: 1px solid var(--at-line); border-radius: 7px; color: var(--at-muted); font-size: 12px; }
.at-card-topline { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.at-example-card { grid-column: 1 / -1; border-inline-start: 3px solid var(--at-accent); }
.at-example-label { display: inline-flex; align-items: center; gap: 5px; margin-inline-end: auto; font-size: 12px; font-weight: 600; color: var(--at-accent); }
.at-example-card .at-card-description { min-height: 0; }
.at-example-card .at-card-footer { flex-wrap: wrap; }
.at-guide-body { border-top: 1px solid var(--at-line); padding: 16px 20px; }
@container (max-width: 480px) {
  .at-example-card .at-guide-toggle { flex-basis: 100%; justify-content: flex-start; }
}
.at-card-description { min-height: 42px; margin-top: 9px; color: var(--at-muted); font-size: 12px; line-height: 1.8; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
.at-card-metadata { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; color: var(--at-muted); font-size: 11px; line-height: 1.7; }
.at-meta-divider { margin-inline: 5px; opacity: .6; }
.at-draft-indicator { display: inline-flex; align-items: center; gap: 5px; }
.at-draft-indicator i { width: 5px; height: 5px; background: #bd8e45; border-radius: 50%; }
.at-card-secondary-actions { display: flex; align-items: center; }
.at-card-icon-action { min-width: 30px; width: 30px; height: 34px; padding: 0; color: var(--at-muted); }
.at-card-icon-action:hover { color: var(--at-accent); }
.at-card-footer { gap: 3px !important; padding: 11px 12px !important; }
.at-card-footer :deep(.v-btn:not(.at-card-icon-action)) { padding-inline: 10px; }
.at-empty-state { padding: 32px; border: 1px dashed var(--at-line); background: var(--at-surface) !important; color: var(--at-muted) !important; }
.at-run-filters { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; margin-bottom: 16px; }
.at-run-filters :deep(.v-btn) { color: var(--at-muted) !important; }
.at-run-filters :deep(.v-btn[aria-pressed='true']) { background: var(--at-surface); color: var(--at-accent) !important; box-shadow: 0 1px 6px #2433420a; }
.at-run-filters :deep(.v-select .v-field) { font-size: 12px; }
.workflow-run-list > summary { cursor: pointer; font-size: 12px !important; font-weight: 550; color: var(--at-muted); padding: 8px 0; }
.workflow-run-list :deep(.v-card) { border: 1px solid var(--at-line); }
.at-run-workspace { overflow: hidden; box-shadow: var(--at-shadow); }
.workbench-body { padding: 0 !important; }
.at-run-header { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 20px 22px 10px; }
.at-run-header h2 { font-size: 18px !important; font-weight: 650 !important; color: var(--at-ink); line-height: 1.5; }
.at-run-header :deep(.v-btn) { padding-inline: 10px; }
.at-run-header :deep(.v-btn[aria-pressed='true']) { background: var(--at-accent-soft); color: var(--at-accent); }
.at-run-goal { padding: 0 22px; color: var(--at-muted); font-size: 12px; line-height: 1.7; margin: 0; }
.at-run-goal:empty { display: none; }
.at-run-snapshot { margin: 0; padding: 10px 22px 16px; color: var(--at-muted); font-size: 11px; border-bottom: 1px solid var(--at-line); }
.workbench-columns { display: grid; grid-template-columns: 220px minmax(0, 1fr) 310px; align-items: stretch; gap: 0; }
.workbench-column { min-width: 0; max-height: max(520px, calc(100dvh - 300px)); overflow: auto; scrollbar-width: thin; scrollbar-color: var(--at-line) transparent; }
.workbench-tasks { padding: 18px 12px; background: color-mix(in srgb, var(--at-paper) 45%, var(--at-surface)); border-right: 1px solid var(--at-line); }
.workbench-process { padding: 18px 22px 26px; min-height: 520px; }
.workbench-results { padding: 20px 16px; border-left: 1px solid var(--at-line); background: color-mix(in srgb, var(--at-paper) 25%, var(--at-surface)); font-size: 12px; }
.workbench-results :deep(.text-h6), .at-artifacts-heading { font-size: 13px !important; font-weight: 650; }
.workbench-results :deep(.text-body-2) { font-size: 12px !important; line-height: 1.8; }
.workbench-results > details { border-top: 1px solid var(--at-line); padding-top: 12px; color: var(--at-muted); font-size: 11px; }
.workbench-results summary { cursor: pointer; }
.at-human-section { margin-bottom: 22px; }
.at-human-section > p { color: var(--at-muted); font-size: 11px !important; line-height: 1.75; margin: 6px 0 14px; }
.at-artifacts-heading { margin: 24px 0 14px; padding-top: 18px; border-top: 1px solid var(--at-line); }
.at-process-history { border-bottom: 1px solid var(--at-line); padding-bottom: 12px; margin-bottom: 18px; color: var(--at-muted); }
.at-process-history > summary { display: flex; align-items: center; gap: 6px; cursor: pointer; width: fit-content; font-size: 11px; }
.at-process-history > p { font-size: 11px !important; margin-top: 10px; }
.at-history-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.at-history-actions :deep(.v-btn) { height: 30px; padding-inline: 8px; font-size: 11px; }
.workbench-panel-nav { display: none; }
.workflow-text { white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; max-width: 100%; font: inherit; }
.workflow-name { min-width: 0; overflow-wrap: anywhere; }
.workflow-result { max-height: 28rem; overflow: auto; padding: 14px; background: var(--at-paper); border: 1px solid var(--at-line); border-radius: 9px; line-height: 1.8; font-size: 12px; }
@container (max-width: 1000px) {
  .workbench-columns { grid-template-columns: minmax(0, 1fr); }
  .workbench-column { max-height: 70vh; }
  .workbench-process { min-height: 400px; }
  .workbench-panel-nav { display: flex; flex-wrap: wrap; gap: 4px; padding: 12px 18px; margin-bottom: 0 !important; border-bottom: 1px solid var(--at-line); }
  .workbench-columns:not([data-active-panel='tasks']) > .workbench-tasks,
  .workbench-columns:not([data-active-panel='process']) > .workbench-process,
  .workbench-columns:not([data-active-panel='results']) > .workbench-results { display: none; }
  .workbench-tasks, .workbench-results { border: 0; }
}
@media (max-width: 1000px) {
}
@media (max-width: 600px) {
  .at-card-icon-action { width: 34px; height: 38px; }
  .at-run-header { padding: 16px; gap: 8px; }
  .at-run-header h2 { flex-basis: 100%; }
  .at-run-goal, .at-run-snapshot { padding-inline: 16px; }
  .workbench-process, .workbench-results { padding: 16px; }
  .workbench-panel-nav { display: grid; grid-template-columns: 1fr 1fr 1fr; padding: 8px; }
  .workbench-panel-nav :deep(.v-btn) { min-width: 0; padding-inline: 6px; font-size: 12px; }
}
</style>

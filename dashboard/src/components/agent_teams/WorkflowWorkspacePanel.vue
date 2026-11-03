<template>
  <section class="workflow-workspace">
    <div v-if="mode === 'assets' || !selectedRun" class="at-asset-toolbar">
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
      <span v-if="mode === 'assets'" class="at-asset-count">{{ visibleAssets.length + visibleDrafts.length }}</span>
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
    <p v-if="mode === 'assets' || !selectedRun" class="at-asset-intro">
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
      <v-alert v-if="!visibleAssets.length && !visibleDrafts.length && !loading" type="info" variant="tonal" class="at-empty-state">{{ tm("v2.emptyAssets") }}</v-alert>
      <div class="at-asset-grid">
        <v-card v-for="item in visibleDrafts" :key="item.asset_id" class="at-asset-card at-draft-card" variant="outlined">
          <v-card-text>
            <div class="at-card-topline">
              <span class="at-asset-icon" :class="{ 'is-team': item.kind === 'autonomous' }" aria-hidden="true"><v-icon :icon="item.kind === 'autonomous' ? 'mdi-account-group-outline' : 'mdi-sitemap-outline'" size="23" /></span>
              <v-chip size="small" color="warning" variant="tonal">{{ tm("draft.unpublished") }}</v-chip>
            </div>
            <v-card-title>{{ item.body.definition?.name || tm("draft.untitled") }}</v-card-title>
            <p class="at-card-description">{{ tm(item.kind === 'autonomous' ? "workbench.teamIntro" : "workbench.workflowIntro") }}</p>
          </v-card-text>
          <v-card-actions>
            <v-btn variant="tonal" color="primary" prepend-icon="mdi-pencil-outline" @click="item.kind === 'autonomous' ? editTeam({ asset_id: item.asset_id, definition: item.body.definition }) : editGraph({ asset_id: item.asset_id, definition: item.body.definition })">{{ tm("v2.edit") }}</v-btn>
            <v-spacer />
            <v-btn data-testid="delete-asset" variant="text" color="error" :aria-label="tm('deletion.delete')" :title="tm('deletion.delete')" class="at-card-icon-action" @click="deleteTarget = { asset_id: item.asset_id, definition: item.body.definition }"><v-icon icon="mdi-trash-can-outline" size="18" /><span class="at-sr-only">{{ tm('deletion.delete') }}</span></v-btn>
          </v-card-actions>
        </v-card>
        <v-card v-for="asset in visibleAssets" :key="asset.asset_id" class="at-asset-card" variant="outlined">
          <v-card-text>
            <div class="at-card-topline">
              <span class="at-asset-icon" :class="{ 'is-team': asset.definition.kind === 'autonomous' }" aria-hidden="true"><v-icon :icon="asset.definition.kind === 'autonomous' ? 'mdi-account-group-outline' : 'mdi-sitemap-outline'" size="23" /></span>
              <v-chip size="small" variant="tonal">v{{ asset.version }}</v-chip>
            </div>
            <v-card-title>{{ asset.definition.name }}</v-card-title>
            <p class="at-card-description">{{ asset.definition.kind === 'autonomous' ? asset.definition.allowed_roles.map((member: any) => member.role?.name || roles.find((role: any) => role.role_id === member.role_id)?.definition.name || member.id).filter(Boolean).join(' · ') : asset.definition.tasks.map((task: any) => task.name).slice(0, 4).join(' · ') }}</p>
            <div class="at-card-metadata">
              <span v-if="asset.definition.kind === 'autonomous'"><v-icon icon="mdi-account-multiple-outline" size="15" /> {{ tm("autonomous.allowedRoles") }} · {{ asset.definition.allowed_roles.length }}</span>
              <span v-else><v-icon icon="mdi-transit-connection-variant" size="15" /> {{ tm("graph.count", { count: asset.definition.tasks.length }) }} <span class="at-meta-divider">·</span> {{ tm("graph.parallel") }} {{ asset.definition.max_parallel }}</span>
              <span v-if="drafts.some((item) => item.asset_id === asset.asset_id && item.published_revision !== item.revision)" class="at-draft-indicator" data-testid="editing-draft"><i />{{ tm("draft.editing") }}</span>
            </div>
          </v-card-text>
          <v-card-actions class="at-card-footer">
            <div class="at-card-secondary-actions">
              <v-btn variant="text" class="at-card-icon-action" data-testid="open-chat-access" :aria-label="tm('chatAccess.title')" :title="tm('chatAccess.title')" @click="chatAccessAsset = asset"><v-icon icon="mdi-link-variant" size="18" /><span class="at-sr-only">{{ tm("chatAccess.title") }}</span></v-btn>
              <v-btn variant="text" class="at-card-icon-action" :aria-label="tm('package.export')" :title="tm('package.export')" @click="exportWorkflow(asset)"><v-icon icon="mdi-export-variant" size="18" /><span class="at-sr-only">{{ tm("package.export") }}</span></v-btn>
              <v-btn data-testid="delete-asset" variant="text" class="at-card-icon-action" :aria-label="tm('deletion.delete')" :title="tm('deletion.delete')" @click="deleteTarget = asset"><v-icon icon="mdi-trash-can-outline" size="18" /><span class="at-sr-only">{{ tm('deletion.delete') }}</span></v-btn>
            </div>
            <v-spacer />
            <v-btn variant="text" @click="asset.definition.kind === 'autonomous' ? editTeam(asset) : editGraph(asset)">{{ tm("v2.edit") }}</v-btn>
            <v-btn variant="tonal" color="primary" prepend-icon="mdi-play-outline" @click="prepareRun(asset)">{{ tm("v2.start") }}</v-btn>
          </v-card-actions>
        </v-card>
      </div>
    </template>

    <DeleteAssetDialog :name="deleteTarget?.definition.name || (deleteTarget ? tm('draft.untitled') : null)" :busy="deleting" :error="deleteError" @cancel="deleteTarget = null; deleteError = ''" @confirm="deleteAsset" />
    <template v-if="mode !== 'assets'">
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
              <p v-if="run.artifacts?.length" class="text-caption mt-1">
                {{ tm("progress.artifacts") }}: {{ run.artifacts.length }}
              </p>
            </div>
            <v-btn variant="text" @click="openRun(run.run_id)">{{
              tm("v2.details")
            }}</v-btn>
          </v-card-text>
        </v-card>
      </details>
      <v-card
        v-if="selectedRun && runView !== 'history'"
        variant="outlined"
        class="at-run-workspace"
        data-testid="run-detail"
      >
        <v-card-text class="workbench-body">
          <div class="at-run-header">
            <h2 class="text-h5 font-weight-bold flex-grow-1">{{ selectedRun.snapshot.definition.name }}</h2>
            <v-chip v-if="selectedRun.snapshot.trial" size="small" color="primary" variant="outlined">{{ tm('trial.test') }} · r{{ selectedRun.snapshot.trial.draft_revision }}</v-chip>
            <v-chip variant="tonal" :color="selectedRun.status === 'completed' ? 'success' : 'primary'">{{ tm(`v2.states.${selectedRun.status}`) }}</v-chip>
            <v-btn variant="text" :aria-pressed="runDisplay === 'workbench'" @click="runDisplay = 'workbench'">{{ tm('workbench.workspace') }}</v-btn>
            <v-btn variant="text" :aria-pressed="runDisplay === 'graph'" @click="runDisplay = 'graph'">{{ tm('progress.graph') }}</v-btn>
          </div>
          <p class="workflow-text at-run-goal" data-testid="run-goal">{{ selectedRun.snapshot.task ?? Object.values(selectedRun.snapshot.inputs ?? {}).join(' · ') }}</p>
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
                :selected-task="selectedTaskNode"
                @select="selectRunTask"
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
              <WorkflowRunProcess :run="detailRun" :busy="!!currentOperation" @resolve="resolveTool" />
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
            v-if="
              detailRun.recovery &&
              ['interrupted', 'failed', 'blocked'].includes(detailRun.status)
            "
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
              v-if="detailRun.recovery.available"
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
          <details v-if="!['interrupted', 'failed', 'blocked'].includes(detailRun.status)" class="my-3">
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
            <RunUsagePanel :run="detailRun" />
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
            @update:model-value="loadArtifact"
          />
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
            v-if="selectedArtifact"
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
            {{ runAsset.definition.name }} · v{{ runAsset.version }}
          </p>
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
            item-title="label"
            item-value="value"
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
            :disabled="checkingReadiness || !readiness?.ready"
            @click="start"
            >{{ tm("v2.start") }}</v-btn
          >
        </v-card-actions>
      </v-card>
    </v-dialog>
    <v-dialog
      class="workbench-dialog"
      scrollable
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
          ><v-spacer /><v-btn variant="text" @click="previewing = false">{{
            tm("v2.close")
          }}</v-btn></v-card-actions
        >
      </v-card>
    </v-dialog>
    <WorkflowPackageDialog
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
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { roleAssetsApi } from "@/api/roleAssets";
import { draftApi } from "@/composables/useAssetDraft";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";
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
  WorkflowRetryRequest,
  HumanAnswerRequest,
  WorkflowPackage,
} from "@/api/generated/openapi-v1";

const props = defineProps<{
  mode: "assets" | "runs";
  assetKind?: "teams" | "workflows";
}>();
const emit = defineEmits<{
  (event: "started", runId: string): void;
  (event: "installed", directory: "teams" | "workflows"): void;
}>();
const { tm } = useModuleI18n("features/agent-teams");
const route = useRoute();
const router = useRouter();
const assets = ref<any[]>([]);
const deleteTarget = ref<any>(null);
const deleting = ref(false);
const deleteError = ref("");
async function deleteAsset() {
  if (!deleteTarget.value) return;
  deleting.value = true;
  deleteError.value = "";
  try {
    await workflowAssetsApi.delete(deleteTarget.value.asset_id);
    deleteTarget.value = null;
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
  if (inboxBusy.value) return;
  inboxBusy.value = true;
  try {
    const updated = await workflowAssetsApi.answer(rootId, todoId, payload);
    if (selectedRun.value?.run_id === rootId) selectedRun.value = updated;
    humanRequests.value = (await workflowAssetsApi.humanRequests()).requests;
  } catch (err) {
    error.value = extractApiError(err, tm("errors.operationFailed")).message;
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
const selectedRun = ref<any>(null);
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
watch(() => selectedRun.value?.run_id, () => {
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
  if (historyBusy.value || !selectedRun.value || !detailRun.value) return;
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
    const page = await workflowAssetsApi.processPage(rootId, taskId, before);
    if (selectedRun.value?.run_id !== rootId || detailRun.value?.run_id !== taskId || !historyReading.value[taskId]) return;
    const pane = processPane.value;
    const height = pane?.scrollHeight ?? 0;
    const top = pane?.scrollTop ?? 0;
    const targets = runViews(selectedRun.value);
    for (const target of targets.filter((run: any) => run.run_id === taskId)) {
      mergeHistoryWindow(target, "process", page.records, true);
    }
    historyDone.value = { ...historyDone.value, [taskId]: page.next_before === null };
    await nextTick();
    if (pane && detailRun.value?.run_id === taskId) pane.scrollTop = top + pane.scrollHeight - height;
  } catch (cause) {
    historyErrors.value[taskId] = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    historyBusy.value = false;
  }
}
async function loadOlderHistory(kind: "events" | "messages" | "attempts") {
  if (historyBusy.value || !selectedRun.value || !detailRun.value) return;
  const rootId = selectedRun.value.run_id;
  const taskId = detailRun.value.run_id;
  const key = `${taskId}:${kind}`;
  const next = historyNext.value[key];
  if (next === null) return;
  const before = next ?? String(detailRun.value[kind === "events" ? "event_history_before" : kind === "messages" ? "message_history_before" : "attempt_history_before"] ?? 1);
  historyBusy.value = true;
  historyReading.value[taskId] = true;
  historyPinned.value[taskId] = true;
  followProcess.value = false;
  delete historyErrors.value[taskId];
  try {
    const page = await workflowAssetsApi.runHistoryPage(rootId, taskId, kind, before);
    if (selectedRun.value?.run_id !== rootId || detailRun.value?.run_id !== taskId || !historyReading.value[taskId]) return;
    const pane = processPane.value;
    const height = pane?.scrollHeight ?? 0;
    const top = pane?.scrollTop ?? 0;
    for (const target of runViews(selectedRun.value).filter((view: any) => view.run_id === taskId)) {
      mergeHistoryWindow(target, kind, page.items, true);
    }
    historyNext.value[key] = page.next_before;
    await nextTick();
    if (pane && detailRun.value?.run_id === taskId) pane.scrollTop = top + pane.scrollHeight - height;
  } catch (cause) {
    historyErrors.value[taskId] = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    historyBusy.value = false;
  }
}
async function restoreLatest(taskId: string) {
  const root = selectedRun.value;
  if (!root || historyRestoring.value[taskId]) return;
  const rootId = root.run_id;
  const boundary = root.cursor;
  historyRestoring.value[taskId] = true;
  delete historyErrors.value[taskId];
  try {
    const [process, events, messages, attempts] = await Promise.all([
      workflowAssetsApi.processPage(rootId, taskId, boundary + 1),
      workflowAssetsApi.runHistoryPage(rootId, taskId, "events", String(boundary + 1)),
      workflowAssetsApi.runHistoryPage(rootId, taskId, "messages", String(boundary + 1)),
      workflowAssetsApi.runHistoryPage(rootId, taskId, "attempts", String(boundary + 1)),
    ]);
    if (selectedRun.value?.run_id !== rootId || historyReading.value[taskId]) return;
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
      if (detailRun.value?.run_id === taskId && followProcess.value && processPane.value)
        processPane.value.scrollTop = processPane.value.scrollHeight;
    }
  } catch (cause) {
    if (selectedRun.value?.run_id === rootId) {
      historyErrors.value[taskId] = extractApiError(cause, tm("errors.operationFailed")).message;
      if (detailRun.value?.run_id === taskId) {
        historyReading.value[taskId] = true;
        followProcess.value = false;
      }
    }
  } finally {
    delete historyRestoring.value[taskId];
    delete historyPending.value[taskId];
  }
}
async function reloadLatestProcess() {
  const taskId = detailRun.value?.run_id;
  if (!taskId || historyBusy.value || historyRestoring.value[taskId]) return;
  delete historyReading.value[taskId];
  historyReturning.value[taskId] = true;
  followProcess.value = false;
  try {
    await restoreLatest(taskId);
  } finally {
    delete historyReturning.value[taskId];
  }
}
const teamEditing = ref(false);
const selectedArtifact = ref<any>(null);
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
const artifactVersion = ref<number>(1);
const inputArtifacts = ref<string[]>([]);
const revising = ref(false);
const revisionText = ref("");
const revisionFiles = ref<File[]>([]);
const revisionRequestId = ref("");
const previewing = ref(false);
const previewText = ref("");
const previewUrl = ref("");
const previewBlob = ref<Blob | null>(null);
const previewFilename = ref("");
const availableInputs = computed(() =>
  runs.value.flatMap((run) =>
    (run.artifacts ?? []).map((artifact: any) => ({
      label: `${run.snapshot.definition.name} · v${artifact.version} · ${artifact.summary}`,
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
let timer: ReturnType<typeof setInterval> | undefined;
let disposed = false;
let artifactRead = 0;
let runRead = 0;
let refreshingRead: number | null = null;
let openingRun = false;

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
    loading.value ||
    refreshingRead === runRead ||
    currentOperation.value ||
    openingRun ||
    (background && runView.value === "history")
  )
    return;
  const read = ++runRead;
  refreshingRead = read;
  if (!background) loading.value = true;
  try {
    if (props.mode === "assets") {
      const [assetData, roleData, runData, resources, workflowDrafts, teamDrafts] = await Promise.all([
        workflowAssetsApi.list(),
        roleAssetsApi.list(),
        workflowAssetsApi.runs(),
        roleAssetsApi.resources(),
        draftApi.list("workflow"),
        draftApi.list("autonomous"),
      ]);
      if (disposed || read !== runRead) return;
      assets.value = assetData.assets;
      drafts.value = [...workflowDrafts.map((item) => ({ ...item, kind: "workflow" })), ...teamDrafts.map((item) => ({ ...item, kind: "autonomous" }))];
      roles.value = roleData.roles;
      runs.value = runData.runs;
      if (resources) catalog.value = resources;
    } else {
      const [runData, inboxData] = await Promise.all([
        workflowAssetsApi.runs(),
        workflowAssetsApi.humanRequests(),
      ]);
      if (
        disposed ||
        read !== runRead ||
        (background && runView.value === "history")
      )
        return;
      runs.value = runData.runs;
      humanRequests.value = inboxData.requests;
      const id = route.query.workflow_run ?? selectedRun.value?.run_id;
      if (typeof id === "string" && id) {
        const artifactId = detailRun.value?.result_ref?.artifact_id;
        if (
          selectedRun.value?.run_id === id &&
          selectedRun.value.cursor !== undefined
        ) {
          const changes = await workflowAssetsApi.events(
            id,
            selectedRun.value.cursor,
          );
          if (disposed || read !== runRead || (background && runView.value === "history")) return;
          if (!changes.snapshot && changes.events?.some((event: any) =>
            typeof event.kind === "string" && !event.kind.startsWith("process.") &&
            !event.kind.startsWith("checkpoint.") &&
            !["usage.started", "usage.reported", "usage.resumed"].includes(event.kind),
          )) {
            const state = await workflowAssetsApi.run(id);
            if (disposed || read !== runRead || (background && runView.value === "history")) return;
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
                    const incoming = [...(target[kind] ?? [])];
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
          const snapshot = await workflowAssetsApi.run(id);
          if (
            disposed ||
            read !== runRead ||
            (background && runView.value === "history")
          )
            return;
          selectedRun.value = snapshot;
          selectedTaskNode.value = snapshot.tasks?.[0]?.run_id ?? null;
        }
        if (artifactId !== detailRun.value.result_ref?.artifact_id) {
          artifactVersion.value = detailRun.value.result_ref?.version ?? 1;
        }
        if (
          !selectedArtifact.value ||
          selectedArtifact.value.artifact_id !==
            detailRun.value.result_ref?.artifact_id
        ) {
          if (!(await loadArtifact())) return;
        }
      }
    }
    if (!disposed && read === runRead) error.value = "";
  } catch (err) {
    if (
      !disposed &&
      read === runRead &&
      !(background && runView.value === "history")
    )
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    if (read === runRead) loading.value = false;
    if (refreshingRead === read) refreshingRead = null;
  }
}

function editGraph(asset: any) {
  void router.push({ name: 'AgentWorkflowEditor', params: { assetId: asset?.asset_id ?? 'new' } });
}

watch(teamEditing, (open, previous) => { if (!open && previous) void refresh(); });

function editTeam(asset: any) {
  editingAsset.value = asset;
  teamEditing.value = true;
}

function selectRunTask(nodeId: string | null) {
  runDisplay.value = 'workbench';
  activeRunPanel.value = 'process';
  if (nodeId)
    selectedTaskNode.value =
      allRunTasks.value.find((task) => task.run_id === nodeId)?.run_id ??
      selectedRun.value?.tasks?.find((task: any) => task.node_id === nodeId)
        ?.run_id ??
      nodeId;
}
watch(
  () => detailRun.value?.run_id,
  async () => {
    artifactRead++;
    selectedArtifact.value = null;
    artifactVersion.value = detailRun.value?.result_ref?.version ?? 1;
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

function prepareRun(asset: any) {
  runAsset.value = asset;
  inputs.value = Object.fromEntries(
    asset.definition.required_inputs.map((name: string) => [name, ""]),
  );
  formError.value = "";
  starting.value = true;
  inputArtifacts.value = [];
  void checkReadiness();
}

async function start() {
  if (saving.value || !runAsset.value) return;
  saving.value = true;
  formError.value = "";
  try {
    if (!(await checkReadiness())) return;
    const run = await workflowAssetsApi.start(runAsset.value.asset_id, {
      version: runAsset.value.version,
      inputs: inputs.value,
      source: "dashboard",
      ...(inputArtifacts.value.length
        ? {
            input_artifacts: inputArtifacts.value.map((value) =>
              JSON.parse(value),
            ),
          }
        : {}),
    });
    starting.value = false;
    await router.replace({
      query: { ...route.query, workflow_run: run.run_id },
    });
    emit("started", run.run_id);
  } catch (err) {
    const failure = extractApiError(err, tm("errors.operationFailed"));
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
  if (previous?.run_id === snapshot.run_id && Number.isInteger(previous.cursor) && snapshot.cursor >= previous.cursor) {
    const oldViews = runViews(previous);
    for (const target of runViews(snapshot)) {
      const old = oldViews.find((view: any) => view.run_id === target.run_id);
      for (const kind of ["process", "events", "messages", "attempts"] as const) {
        const incoming = target[kind] ?? [];
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
  const read = ++runRead;
  openingRun = true;
  loading.value = true;
  try {
    error.value = "";
    const snapshot = await workflowAssetsApi.run(runId);
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
    selectedArtifact.value = null;
    await loadArtifact();
  } catch (err) {
    if (!disposed && read === runRead)
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    if (read === runRead) {
      openingRun = false;
      loading.value = false;
    }
  }
}

async function recoverAttempt() {
  if (
    currentOperation.value ||
    openingRun ||
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
    const snapshot =
      rootId === taskId ? recovered : await workflowAssetsApi.run(rootId);
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
  if (!taskId || openingRun || currentOperation.value) return;
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
    const snapshot =
      rootId === taskId ? resolved : await workflowAssetsApi.run(rootId);
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
  if (!rootId || openingRun || currentOperation.value) return;
  const read = ++runRead;
  runOperations.value.set(rootId, { kind: "answer", taskId: todoId });
  loading.value = true;
  try {
    error.value = "";
    const updated = await workflowAssetsApi.answer(rootId, todoId, payload);
    if (!disposed && read === runRead) applyOperationSnapshot(updated);
  } catch (err) {
    // Returning to the same run keeps its pending response and failure visible.
    if (!disposed && !openingRun && selectedRun.value?.run_id === rootId)
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    if (read === runRead) loading.value = false;
    runOperations.value.delete(rootId);
  }
}

async function applyRunControl(
  payload: WorkflowControlRequest | WorkflowRetryRequest,
) {
  const rootId = selectedRun.value?.run_id;
  if (!rootId || openingRun || currentOperation.value) return;
  const taskId = detailRun.value.run_id;
  const read = ++runRead;
  runOperations.value.set(rootId, { kind: "control", taskId });
  loading.value = true;
  try {
    error.value = "";
    const retry = "attempt_id" in payload;
    const updated = retry
      ? await workflowAssetsApi.retry(taskId, payload)
      : await workflowAssetsApi.control(rootId, payload);
    if (disposed || read !== runRead) return;
    const snapshot =
      retry && rootId !== taskId
        ? await workflowAssetsApi.run(rootId)
        : updated;
    if (!disposed && read === runRead) applyOperationSnapshot(snapshot);
  } catch (err) {
    if (!disposed && read === runRead)
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
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
  const read = ++artifactRead;
  const ref = detailRun.value?.result_ref;
  if (!ref) return true;
  selectedArtifact.value = null;
  try {
    const artifact = await workflowAssetsApi.artifact(
      ref.artifact_id,
      Number(artifactVersion.value),
    );
    // A slower response must not overwrite a more recent version selection.
    if (!disposed && read === artifactRead && runView.value !== "history") {
      selectedArtifact.value = artifact;
      return true;
    }
  } catch (err) {
    if (!disposed && read === artifactRead && runView.value !== "history") {
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
    }
  }
  return false;
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

async function readFile(fileId: string, download: boolean) {
  try {
    const ref = selectedArtifact.value;
    const response = await workflowAssetsApi.file(
      ref.artifact_id,
      ref.version,
      fileId,
      download,
    );
    if (download) {
      const url = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      const disposition = response.headers["content-disposition"] ?? "";
      link.download = decodeURIComponent(
        disposition.split("filename*=UTF-8''")[1] ?? fileId,
      );
      link.href = url;
      link.click();
      URL.revokeObjectURL(url);
    } else {
      if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
      previewText.value = response.data.type.startsWith("text/")
        ? await response.data.text()
        : "";
      previewBlob.value = response.data.type.startsWith("image/") || response.data.type === "application/pdf"
        ? response.data : null;
      const files = ref.files ?? ref.content?.files ?? [];
      previewFilename.value = files.find((file: any) => file.file_id === fileId)?.filename ?? fileId;
      previewUrl.value = previewText.value || previewBlob.value
        ? ""
        : URL.createObjectURL(response.data);
      previewing.value = true;
    }
  } catch (err) {
    error.value = extractApiError(err, tm("errors.operationFailed")).message;
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
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
});
</script>

<style scoped>
.workflow-workspace { min-width: 0; container-type: inline-size; }
.at-asset-count { display: grid; place-items: center; min-width: 24px; height: 24px; padding: 0 7px; border: 1px solid var(--at-line); border-radius: 7px; color: var(--at-muted); font-size: 12px; }
.at-card-topline { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
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

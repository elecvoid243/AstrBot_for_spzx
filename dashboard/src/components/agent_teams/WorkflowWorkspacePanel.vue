<template>
  <section class="workflow-workspace">
    <div class="d-flex flex-wrap align-center ga-2 mb-4">
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
      <v-spacer />
      <v-btn
        v-if="mode === 'assets'"
        variant="tonal"
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
        data-testid="create-autonomous-team"
        @click="editTeam(null)"
        >{{ tm("autonomous.create") }}</v-btn
      >
      <v-btn
        v-if="mode === 'assets' && assetKind !== 'teams'"
        variant="tonal"
        color="primary"
        @click="editGraph(null)"
        >{{ tm("graph.create") }}</v-btn
      >
      <v-btn
        variant="text"
        :loading="loading || !!currentOperation"
        @click="refresh"
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
    <p class="text-body-2 text-medium-emphasis mb-4">
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
      <v-alert
        v-if="!visibleAssets.length && !visibleDrafts.length && !loading"
        type="info"
        variant="tonal"
        >{{ tm("v2.emptyAssets") }}</v-alert
      >
      <v-card
        v-for="item in visibleDrafts"
        :key="item.asset_id"
        class="mb-3"
        variant="outlined"
      >
        <v-card-title
          >{{ item.body.definition?.name || tm("draft.untitled") }} ·
          {{ tm("draft.unpublished") }}</v-card-title
        >
        <v-card-actions
          ><v-btn
            variant="text"
            @click="
              item.kind === 'autonomous'
                ? editTeam({
                    asset_id: item.asset_id,
                    definition: item.body.definition,
                  })
                : editGraph({
                    asset_id: item.asset_id,
                    definition: item.body.definition,
                  })
            "
            >{{ tm("v2.edit") }}</v-btn
          ></v-card-actions
        >
      </v-card>
      <v-card
        v-for="asset in visibleAssets"
        :key="asset.asset_id"
        class="mb-3"
        variant="outlined"
      >
        <v-card-title class="text-wrap"
          >{{ asset.definition.name }} · v{{ asset.version }}</v-card-title
        >
        <v-card-text class="text-wrap">
          <p v-if="drafts.some((item) => item.asset_id === asset.asset_id)" data-testid="editing-draft">{{ tm("draft.editing") }}</p>
          <p v-if="asset.definition.kind === 'autonomous'">
            {{ tm("autonomous.title") }} · {{ tm("autonomous.allowedRoles") }}:
            {{ asset.definition.allowed_roles.length }}
          </p>
          <p v-else-if="asset.definition.tasks">
            {{ tm("graph.count", { count: asset.definition.tasks.length }) }} ·
            {{ tm("graph.parallel") }}: {{ asset.definition.max_parallel }}
          </p>
          <p v-else>
            {{ tm("v2.role") }}:
            {{
              roles.find((role) => role.role_id === asset.definition.role_id)
                ?.definition.name ?? asset.definition.role_id
            }}
            · v{{ asset.definition.role_version }}
          </p>
          <p class="workflow-text mt-2">{{ asset.definition.task }}</p>
        </v-card-text>
        <v-card-actions class="flex-wrap">
          <v-btn
            v-if="
              asset.definition.kind === 'autonomous' || asset.definition.tasks
            "
            variant="text"
            data-testid="open-chat-access"
            @click="chatAccessAsset = asset"
            >{{ tm("chatAccess.title") }}</v-btn
          >
          <v-btn variant="text" @click="exportWorkflow(asset)">{{
            tm("package.export")
          }}</v-btn>
          <v-btn
            variant="text"
            @click="
              asset.definition.kind === 'autonomous'
                ? editTeam(asset)
                : editGraph(asset)
            "
            >{{ tm("v2.edit") }}</v-btn
          >
          <v-btn variant="tonal" color="primary" @click="prepareRun(asset)">{{
            tm("v2.start")
          }}</v-btn>
        </v-card-actions>
      </v-card>
    </template>

    <template v-else>
      <nav
        class="d-flex flex-wrap ga-2 mb-4"
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
      </nav>
      <section
        v-if="runView === 'pending'"
        :aria-label="tm('human.inbox')"
        data-testid="human-inbox"
      >
        <h3 class="text-h6">{{ tm("human.inbox") }} · {{ humanRequests.length }}</h3>
        <div v-for="todo in humanRequests" :key="todo.todo_id">
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
        class="mt-4"
        data-testid="run-detail"
      >
        <v-card-text>
          <v-alert
            type="info"
            variant="tonal"
            class="mb-4"
            icon="mdi-lock-outline"
            >{{ tm("workbench.snapshotHint") }}</v-alert
          >
          <div class="workbench-columns">
            <div class="workbench-column">
              <WorkflowRunProgress
                :run="selectedRun"
                :selected-task="selectedTaskNode"
                @select="selectRunTask"
              />
            </div>
            <div ref="processPane" class="workbench-column workbench-process" @scroll="onProcessScroll">
              <p class="text-caption text-medium-emphasis">{{ tm("progress.historyHint") }}</p>
              <v-btn variant="tonal" :disabled="historyBusy || historyDone[detailRun.run_id]" @click="loadOlderProcess">{{ tm("progress.loadOlder") }}</v-btn>
              <v-btn variant="text" :disabled="historyBusy || historyNext[detailRun.run_id + ':messages'] === null" @click="loadOlderHistory('messages')">{{ tm("progress.loadMessages") }}</v-btn>
              <v-btn variant="text" :disabled="historyBusy || historyNext[detailRun.run_id + ':events'] === null" @click="loadOlderHistory('events')">{{ tm("progress.loadEvents") }}</v-btn>
              <v-btn variant="text" :disabled="historyBusy || historyNext[detailRun.run_id + ':attempts'] === null" @click="loadOlderHistory('attempts')">{{ tm("progress.loadAttempts") }}</v-btn>
              <v-btn v-if="historyTruncated[detailRun.run_id]" variant="text" @click="reloadLatestProcess">{{ tm("progress.reloadLatest") }}</v-btn>
              <p v-if="historyTruncated[detailRun.run_id]" class="text-warning">{{ tm("progress.historyWindow") }}</p>
              <p v-if="historyErrors[detailRun.run_id]" role="alert">{{ historyErrors[detailRun.run_id] }}</p>
              <WorkflowRunProcess :run="detailRun" :busy="!!currentOperation" @resolve="resolveTool" />
            </div>
            <div class="workbench-column">
          <section
            v-if="selectedRun.todos?.length"
            :aria-label="tm('human.title')"
            class="my-4"
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
          <h3 class="text-h6 mt-6 mb-3">{{ tm("progress.artifacts") }}</h3>
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
          <RunnerCapabilities
            v-if="
              !['interrupted', 'failed', 'blocked'].includes(detailRun.status)
            "
            :runner="detailRun.snapshot.effective?.runner"
            :identity="detailRun.recovery?.remote_identity"
            class="my-3"
          />
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
          <p
            v-if="detailRun.snapshot.input_artifacts?.length"
            class="workflow-text mt-3"
          >
            {{ tm("v2.pinnedInputs") }}:
            {{ JSON.stringify(detailRun.snapshot.input_artifacts) }}
          </p>
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
      @saved="refresh"
    />
    <v-dialog
      class="workbench-dialog"
      scrollable
      v-model="graphEditing"
      max-width="1600"
      persistent
      content-class="graph-dialog-overlay"
    >
      <v-card class="graph-dialog-card">
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("graph.canvas")
        }}</v-card-title>
        <v-card-text class="graph-dialog-content" :inert="saving || graphDraft.busy.value ? '' : undefined">
          <DraftStatus :draft="graphDraft" @reload="reloadGraphDraft" @discard="graphEditing = false" @use-latest="useLatestDraft" />
          <div class="d-flex flex-wrap ga-3 mb-3">
            <v-text-field
              v-model="graphForm.name"
              :label="tm('v2.name')"
              style="min-width: 180px"
            />
            <v-select
              v-model="graphForm.default_provider_id"
              :items="catalog.models"
              item-title="name"
              item-value="id"
              :label="tm('graph.defaultModel')"
              clearable
              style="min-width: 220px"
            />
            <v-text-field
              v-model.number="graphForm.max_parallel"
              type="number"
              min="1"
              max="100"
              :label="tm('graph.parallel')"
              style="max-width: 220px"
            />
            <v-text-field
              v-model.number="graphForm.max_depth"
              type="number"
              min="1"
              max="20"
              :label="tm('child.maxDepth')"
              style="max-width: 220px"
            />
          </div>
          <details :open="graphUsageInvalid" class="mb-3">
            <summary class="text-subtitle-1">{{ tm("usage.limits") }}</summary>
            <RunLimitsForm
              v-model="graphForm.limits"
              @validation="graphUsageInvalid = $event"
            />
          </details>
          <WorkflowGraphCanvas
            class="graph-dialog-canvas"
            :key="graphEditorKey"
            v-model:definition="graphForm"
            :roles="roles"
            :catalog="catalog"
            :issues="graphIssues"
            :teams="
              assets.filter((asset) => asset.definition.kind === 'autonomous')
            "
            :workflows="assets.filter((asset) => asset.definition.kind !== 'autonomous')"
            @validation="graphInvalid = $event"
            @role-saved="
              roleAssetsApi
                .list()
                .then((data) => (roles = data.roles))
                .catch(
                  (error) =>
                    (formError = extractApiError(
                      error,
                      tm('errors.operationFailed'),
                    ).message),
                )
            "
          />
          <v-alert
            v-if="formError"
            type="error"
            variant="tonal"
            class="mt-3"
            role="alert"
            >{{ formError }}</v-alert
          >
          <p class="text-caption mt-3">{{ tm("graph.saveHint") }}</p>
        </v-card-text>
        <v-card-actions
          ><v-spacer /><v-btn
            variant="text"
            :disabled="saving || graphDraft.busy.value"
            @click="closeGraph"
            >{{ tm("v2.close") }}</v-btn
          ><v-btn
            variant="tonal"
            color="primary"
            :loading="saving"
            :disabled="graphInvalid || graphUsageInvalid || graphDraft.busy.value"
            @click="saveGraph"
            >{{ tm("draft.publish") }}</v-btn
          ></v-card-actions
        >
      </v-card>
    </v-dialog>

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
          <RunnerCapabilities
            v-for="role in readiness?.roles ?? []"
            :key="role.role_id + ':' + role.version"
            :runner="role.runner"
            class="mb-3"
          />
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
import DraftStatus from "./DraftStatus.vue";
import { draftApi, useAssetDraft } from "@/composables/useAssetDraft";
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
import type {
  GraphDefinition,
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
const drafts = ref<any[]>([]);
const graphDraft = useAssetDraft("workflow");
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
  selectedTaskNode.value = todo.recommendation_ref?.artifact_id ?? todo.task_run_id;
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
const visibleRuns = computed(() =>
  runs.value.filter((run) => {
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
  if (pane) followProcess.value = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 48;
}
watch(() => selectedTaskNode.value, () => { followProcess.value = true; });
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
watch(() => selectedRun.value?.run_id, () => {
  historyDone.value = {};
  historyNext.value = {};
  historyErrors.value = {};
  historyTruncated.value = {};
});
async function loadOlderProcess() {
  if (historyBusy.value || !selectedRun.value || !detailRun.value) return;
  const rootId = selectedRun.value.run_id;
  const taskId = detailRun.value.run_id;
  const cursors = (detailRun.value.process ?? []).map((record: any) => record.cursor).filter((cursor: any) => Number.isInteger(cursor));
  const before = cursors.length ? Math.min(...cursors) : selectedRun.value.cursor + 1;
  historyBusy.value = true;
  delete historyErrors.value[taskId];
  try {
    const page = await workflowAssetsApi.processPage(rootId, taskId, before);
    if (selectedRun.value?.run_id !== rootId) return;
    const pane = processPane.value;
    const height = pane?.scrollHeight ?? 0;
    const top = pane?.scrollTop ?? 0;
    const targets = runViews(selectedRun.value);
    for (const target of targets.filter((run: any) => run.run_id === taskId)) {
      const records = new Map([...page.records, ...(target.process ?? [])].map((record: any) => [record.record_id, record]));
      const merged = [...records.values()].sort((a: any, b: any) => a.cursor - b.cursor);
      if (merged.length > 512) historyTruncated.value[taskId] = true;
      target.process = merged.slice(0, 512);
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
  delete historyErrors.value[taskId];
  try {
    const page = await workflowAssetsApi.runHistoryPage(rootId, taskId, kind, before);
    if (selectedRun.value?.run_id !== rootId) return;
    const pane = processPane.value;
    const height = pane?.scrollHeight ?? 0;
    const top = pane?.scrollTop ?? 0;
    for (const target of runViews(selectedRun.value).filter((view: any) => view.run_id === taskId)) {
      const id = kind === "events" ? "cursor" : kind === "messages" ? "message_id" : "attempt_id";
      const entries = new Map([...page.items, ...(target[kind] ?? [])].map((entry: any) => [entry[id], entry]));
      const merged = [...entries.values()].sort((a: any, b: any) => a.cursor - b.cursor);
      if (merged.length > 512) historyTruncated.value[taskId] = true;
      target[kind] = merged.slice(0, 512);
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
async function reloadLatestProcess() {
  const rootId = selectedRun.value?.run_id;
  if (!rootId || historyBusy.value) return;
  historyBusy.value = true;
  const read = ++runRead;
  const taskId = detailRun.value.run_id;
  try {
    const snapshot = await workflowAssetsApi.run(rootId);
    if (read === runRead && selectedRun.value?.run_id === rootId) {
      selectedRun.value = snapshot;
      historyDone.value = {};
      historyNext.value = {};
      historyTruncated.value = {};
      historyErrors.value = {};
    }
  } catch (cause) {
    historyErrors.value[taskId] = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    if (read === runRead) loading.value = false;
    historyBusy.value = false;
  }
}
const graphEditing = ref(false);
const graphEditorKey = ref(0);
const teamEditing = ref(false);
const graphInvalid = ref(false);
const graphIssues = ref<{ path: string; message: string }[]>([]);
const graphUsageInvalid = ref(false);
const graphForm = ref<GraphDefinition>({
  name: "",
  required_inputs: [],
  max_parallel: 4,
  max_depth: 3,
  tasks: [],
  edges: [],
});
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
let openingRun = false;

async function refresh() {
  if (loading.value || currentOperation.value || openingRun) return;
  const read = ++runRead;
  loading.value = true;
  try {
    error.value = "";
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
      if (disposed || read !== runRead) return;
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
          if (disposed || read !== runRead) return;
          if (!changes.snapshot && changes.events?.some((event: any) =>
            typeof event.kind === "string" && !event.kind.startsWith("process.") &&
            !event.kind.startsWith("checkpoint.") &&
            !["usage.started", "usage.reported", "usage.resumed"].includes(event.kind),
          )) {
            const state = await workflowAssetsApi.run(id);
            if (disposed || read !== runRead) return;
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
                  const records = new Map([...(old?.process ?? []), ...(target.process ?? []),
                    ...(changes.records ?? []).filter((record: any) => record.run_id === target.run_id),
                  ].map((record: any) => [record.record_id, record]));
                  const merged = [...records.values()].sort((a: any, b: any) => a.cursor - b.cursor);
                  if (merged.length > 512) historyTruncated.value[target.run_id] = true;
                  target.process = merged.slice(0, 512);
                  const attempts = new Map([...(old?.attempts ?? []), ...(target.attempts ?? [])].map((attempt: any) => [attempt.attempt_id, attempt]));
                  const orderedAttempts = [...attempts.values()].sort((a: any, b: any) => a.cursor - b.cursor);
                  if (orderedAttempts.length > 512) historyTruncated.value[target.run_id] = true;
                  target.attempts = orderedAttempts.slice(0, 512);
                  for (const kind of ["events", "messages"] as const) {
                    const id = kind === "events" ? "cursor" : "message_id";
                    const incoming = kind === "events"
                      ? (changes.events ?? []).filter((event: any) => target.run_id === snapshot.run_id || target.run_id === event.run_id)
                      : (changes.messages ?? []).filter((message: any) => messageBelongs(snapshot, target, message));
                    const entries = new Map([...(old?.[kind] ?? []), ...(target[kind] ?? []), ...incoming].map((entry: any) => [entry[id], entry]));
                    const history = [...entries.values()].sort((a: any, b: any) => a.cursor - b.cursor);
                    if (history.length > 512) historyTruncated.value[target.run_id] = true;
                    target[kind] = history.slice(0, 512);
                  }
                }
              } else {
                historyDone.value = {};
                historyNext.value = {};
                historyTruncated.value = {};
              }
              selectedRun.value = snapshot;
            }
          } else {
            const root = selectedRun.value;
            const runs = runViews(root);
            for (const record of changes.records ?? []) {
              for (const target of runs.filter((run: any) => run.run_id === record.run_id)) {
                target.process ??= [];
                if (!target.process.some((item: any) => item.record_id === record.record_id)) {
                  if (target.process.length < 512) target.process.push(record);
                  else historyTruncated.value[target.run_id] = true;
                }
              }
            }
            for (const target of runs) {
              for (const kind of ["events", "messages"] as const) {
                const id = kind === "events" ? "cursor" : "message_id";
                const incoming = kind === "events"
                  ? (changes.events ?? []).filter((event: any) => target.run_id === root.run_id || target.run_id === event.run_id)
                  : (changes.messages ?? []).filter((message: any) => messageBelongs(root, target, message));
                target[kind] ??= [];
                for (const entry of incoming) {
                  if (!target[kind].some((existing: any) => existing[id] === entry[id])) {
                    if (target[kind].length < 512) target[kind].push(entry);
                    else historyTruncated.value[target.run_id] = true;
                  }
                }
              }
            }
            root.cursor = changes.cursor;
          }
        } else {
          const snapshot = await workflowAssetsApi.run(id);
          if (disposed || read !== runRead) return;
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
          await loadArtifact();
        }
      }
    }
  } catch (err) {
    if (!disposed && read === runRead)
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    if (read === runRead) loading.value = false;
  }
}

async function editGraph(asset: any) {
  graphEditing.value = false;
  graphDraft.close();
  editingAsset.value = asset;
  graphEditorKey.value++;
  graphForm.value = asset
    ? JSON.parse(JSON.stringify(asset.definition))
    : {
        name: "",
        default_provider_id: null,
        required_inputs: ["input"],
        result_task_id: "agent",
        max_parallel: 4,
        max_depth: 3,
        tasks: [
          {
            id: "agent",
            name: "Agent",
            member: "agent",
            role: { name: "Agent" },
            installation: { profile_id: "default" },
            task: "{input}",
            inputs: { input: { input: "input", field: "text" } },
            position: { x: 300, y: 100 },
          },
        ],
        edges: [],
      };
  formError.value = "";
  graphInvalid.value = false;
  graphIssues.value = [];
  try {
    const item = await graphDraft.open(asset?.asset_id ?? null, {
      definition: graphForm.value,
    });
    graphForm.value = item.body.definition;
    await nextTick();
    graphEditing.value = true;
  } catch (err) {
    error.value = extractApiError(err, tm("errors.operationFailed")).message;
  }
}

async function reloadGraphDraft() {
  const item = await graphDraft.reload();
  graphEditing.value = false;
  graphForm.value = item.body.definition;
  graphEditorKey.value++;
  await nextTick();
  graphEditing.value = true;
}

async function useLatestDraft(latest: any) {
  graphForm.value = JSON.parse(JSON.stringify(latest.definition));
  graphEditorKey.value++;
  graphDraft.change({ definition: graphForm.value });
  try { await graphDraft.rebase(); } catch (err) { formError.value = extractApiError(err, tm("errors.operationFailed")).message; }
}

watch(
  [graphEditing, teamEditing],
  ([graph, team], [previousGraph, previousTeam]) => {
    if ((!graph && previousGraph) || (!team && previousTeam)) void refresh();
  },
);

watch(
  graphForm,
  () => {
    if (graphEditing.value) graphDraft.change({ definition: graphForm.value });
  },
  { deep: true },
);

async function closeGraph() {
  if (graphDraft.status.value === "failed" || graphDraft.status.value === "conflict") return;
  try { await graphDraft.flush(); graphEditing.value = false; } catch { /* The draft status retains the failure. */ }
}

function editTeam(asset: any) {
  editingAsset.value = asset;
  teamEditing.value = true;
}

async function saveGraph() {
  if (saving.value || graphInvalid.value || graphUsageInvalid.value) return;
  saving.value = true;
  formError.value = "";
  try {
    graphDraft.change({ definition: graphForm.value });
    await graphDraft.publish();
    graphEditing.value = false;
    await refresh();
  } catch (err) {
    const failure = extractApiError(err, tm("errors.operationFailed"));
    graphIssues.value = failure.fields
      .filter((field) => !field.path.includes(".AutonomousDefinition."))
      .map((field) => ({
        ...field,
        path: field.path
          .replace(/^body\./, "")
          .replace("definition.GraphDefinition.", "definition."),
      }));
    formError.value = `${failure.message} ${graphIssues.value
      .map((field) => `${field.path}: ${field.message}`)
      .join("; ")}`;
  } finally {
    saving.value = false;
  }
}

function selectRunTask(nodeId: string | null) {
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
        const key = kind === "process" ? "record_id" : kind === "events" ? "cursor" : kind === "messages" ? "message_id" : "attempt_id";
        const entries = new Map([...(old?.[kind] ?? []), ...(target[kind] ?? [])].map((entry: any) => [entry[key], entry]));
        const ordered = [...entries.values()].sort((a: any, b: any) => a.cursor - b.cursor);
        if (ordered.length > 512) historyTruncated.value[target.run_id] = true;
        target[kind] = ordered.slice(0, 512);
      }
    }
    // A newer state snapshot does not acknowledge events that have not been read.
    snapshot.cursor = previous.cursor;
  } else {
    historyDone.value = {};
    historyNext.value = {};
    historyTruncated.value = {};
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

async function loadArtifact() {
  const read = ++artifactRead;
  const ref = detailRun.value?.result_ref;
  if (!ref) return;
  selectedArtifact.value = null;
  try {
    const artifact = await workflowAssetsApi.artifact(
      ref.artifact_id,
      Number(artifactVersion.value),
    );
    // A slower response must not overwrite a more recent version selection.
    if (read === artifactRead) selectedArtifact.value = artifact;
  } catch (err) {
    if (read === artifactRead) {
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
    }
  }
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
  if (!disposed && props.mode === "runs") timer = setInterval(refresh, 2000);
});
onUnmounted(() => {
  disposed = true;
  if (timer) clearInterval(timer);
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
});
</script>

<style scoped>
.workbench-columns { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(300px, 1.5fr) minmax(220px, 1fr); gap: 16px; align-items: start; }
.workbench-column { min-width: 0; }
.workbench-process { max-height: 75vh; overflow-y: auto; }
@media (max-width: 1200px) { .workbench-columns { grid-template-columns: 1fr; } .workbench-process { max-height: none; } }

:deep(.graph-dialog-overlay) {
  height: min(900px, calc(100vh - 48px));
}
.graph-dialog-card {
  height: 100%;
  min-height: 0;
}
.graph-dialog-content {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1 1 0;
}
:deep(.graph-dialog-overlay > .graph-dialog-card > .graph-dialog-content) {
  overflow-y: hidden;
}
.graph-dialog-content > :not(.graph-dialog-canvas) {
  flex: 0 0 auto;
}
.graph-dialog-canvas {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
}
.graph-dialog-canvas :deep(.graph-columns) {
  flex: 1 1 0;
  height: auto;
  min-height: 0;
  grid-template-rows: minmax(0, 1fr);
}
@media (max-width: 1000px) {
  :deep(.graph-dialog-overlay > .graph-dialog-card > .graph-dialog-content) {
    overflow-y: auto;
  }
  .graph-dialog-canvas {
    flex: 0 0 auto;
  }
  .graph-dialog-canvas :deep(.graph-columns) {
    flex: 0 0 auto;
    grid-template-rows: none;
  }
}
.workflow-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
  max-width: 100%;
  font: inherit;
}
.workflow-name {
  min-width: 0;
  overflow-wrap: anywhere;
}
.workflow-result {
  max-height: 28rem;
  overflow: auto;
}
</style>

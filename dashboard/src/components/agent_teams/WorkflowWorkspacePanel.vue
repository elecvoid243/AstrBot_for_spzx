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
      <v-btn
        v-if="mode === 'assets' && assetKind !== 'teams'"
        variant="tonal"
        color="primary"
        @click="edit(null)"
        >{{ tm("v2.create") }}</v-btn
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
        v-if="!visibleAssets.length && !loading"
        type="info"
        variant="tonal"
        >{{ tm("v2.emptyAssets") }}</v-alert
      >
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
                : asset.definition.tasks
                ? editGraph(asset)
                : edit(asset)
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
          >{{ tm(`workbench.${view}`) }}</v-btn
        >
      </nav>
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
          <WorkflowRunProgress
            :run="selectedRun"
            :selected-task="selectedTaskNode"
            @select="selectRunTask"
          />
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
              detailRun.snapshot.engine === 'workflow_call_v2' &&
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
                      event.kind.startsWith("result."),
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
          <WorkflowRunProcess
            :run="detailRun"
            :busy="!!currentOperation"
            @resolve="resolveTool"
          />
        </v-card-text>
      </v-card>
    </template>

    <AutonomousTeamDialog
      v-model="teamEditing"
      :asset="editingAsset"
      :roles="roles"
      @saved="refresh"
    />
    <v-dialog
      class="workbench-dialog"
      scrollable
      v-model="graphEditing"
      max-width="1600"
      :persistent="saving"
    >
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("graph.canvas")
        }}</v-card-title>
        <v-card-text>
          <div class="d-flex flex-wrap ga-3 mb-3">
            <v-text-field
              v-model="graphForm.name"
              :label="tm('v2.name')"
              style="min-width: 180px"
            />
            <v-text-field
              v-model="graphInputNames"
              :label="tm('v2.inputNames')"
              style="min-width: 220px"
              @update:model-value="
                graphForm.required_inputs = graphInputNames
                  .split(',')
                  .map((name) => name.trim())
                  .filter(Boolean)
              "
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
            v-model:definition="graphForm"
            :roles="roles"
            :teams="
              assets.filter((asset) => asset.definition.kind === 'autonomous')
            "
            @validation="graphInvalid = $event"
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
            :disabled="saving"
            @click="graphEditing = false"
            >{{ tm("v2.close") }}</v-btn
          ><v-btn
            variant="tonal"
            color="primary"
            :loading="saving"
            :disabled="graphInvalid || graphUsageInvalid"
            @click="saveGraph"
            >{{ tm("v2.save") }}</v-btn
          ></v-card-actions
        >
      </v-card>
    </v-dialog>

    <v-dialog
      class="workbench-dialog"
      scrollable
      v-model="editing"
      max-width="720"
      :persistent="saving"
    >
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("v2.edit")
        }}</v-card-title>
        <v-card-text>
          <v-text-field v-model="form.name" :label="tm('v2.name')" />
          <v-select
            v-model="form.role_id"
            :items="roles"
            item-title="definition.name"
            item-value="role_id"
            :label="tm('v2.role')"
            @update:model-value="selectRole"
          />
          <v-select
            v-model="form.role_version"
            :items="roleVersions"
            :label="tm('v2.roleVersion')"
          />
          <v-text-field
            v-model="inputNames"
            :label="tm('v2.inputNames')"
            :hint="tm('v2.inputHint')"
            persistent-hint
          />
          <v-textarea
            v-model="form.task"
            :label="tm('v2.task')"
            :hint="tm('v2.taskHint')"
            persistent-hint
          />
          <v-textarea
            v-model="contractText"
            :label="tm('v2.contract')"
            :hint="tm('v2.contractHint')"
            persistent-hint
          />
          <RunLimitsForm
            v-model="form.limits"
            @validation="usageInvalid = $event"
          />
          <v-alert v-if="formError" type="error" variant="tonal" class="mt-3">{{
            formError
          }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="saving" @click="editing = false">{{
            tm("v2.close")
          }}</v-btn>
          <v-btn
            variant="tonal"
            color="primary"
            :loading="saving"
            :disabled="usageInvalid"
            @click="save"
            >{{ tm("v2.save") }}</v-btn
          >
        </v-card-actions>
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
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { roleAssetsApi } from "@/api/roleAssets";
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
  WorkflowDefinition,
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
const runs = ref<any[]>([]);
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
const allRunTasks = computed<any[]>(() => [
  ...new Map(
    [
      ...(selectedRun.value?.tasks ?? []),
      ...(selectedRun.value?.scopes ?? []).flatMap((scope: any) => scope.tasks),
      ...(selectedRun.value?.loops ?? []).flatMap((loop: any) =>
        loop.iterations.flatMap((iteration: any) => iteration.tasks),
      ),
    ].map((task: any) => [task.run_id, task]),
  ).values(),
]);
const detailRun = computed(
  () =>
    allRunTasks.value.find(
      (task: any) =>
        task.node_id === selectedTaskNode.value ||
        task.run_id === selectedTaskNode.value,
    ) ??
    selectedRun.value?.tasks?.[0] ??
    selectedRun.value,
);
const graphEditing = ref(false);
const teamEditing = ref(false);
const graphInvalid = ref(false);
const usageInvalid = ref(false);
const graphUsageInvalid = ref(false);
const graphForm = ref<GraphDefinition>({
  name: "",
  required_inputs: [],
  max_parallel: 4,
  max_depth: 3,
  tasks: [],
  edges: [],
});
const graphInputNames = ref("");
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
const contractText = ref("");
const inputArtifacts = ref<string[]>([]);
const revising = ref(false);
const revisionText = ref("");
const revisionFiles = ref<File[]>([]);
const revisionRequestId = ref("");
const previewing = ref(false);
const previewText = ref("");
const previewUrl = ref("");
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
const editing = ref(false);
const starting = ref(false);
const editingAsset = ref<any>(null);
const runAsset = ref<any>(null);
const form = ref<WorkflowDefinition>({
  name: "",
  role_id: "",
  role_version: 1,
  task: "",
  required_inputs: [],
  overrides: {},
});
const inputNames = ref("");
const inputs = ref<Record<string, string>>({});
const roleVersions = computed(
  () =>
    roles.value.find((role) => role.role_id === form.value.role_id)?.versions ??
    [],
);
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
      const [assetData, roleData, runData] = await Promise.all([
        workflowAssetsApi.list(),
        roleAssetsApi.list(),
        workflowAssetsApi.runs(),
      ]);
      if (disposed || read !== runRead) return;
      assets.value = assetData.assets;
      roles.value = roleData.roles;
      runs.value = runData.runs;
    } else {
      const runData = await workflowAssetsApi.runs();
      if (disposed || read !== runRead) return;
      runs.value = runData.runs;
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
          if (
            changes.snapshot &&
            (changes.reset || changes.cursor > selectedRun.value.cursor)
          )
            selectedRun.value = changes.snapshot;
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

function edit(asset: any) {
  editingAsset.value = asset;
  form.value = asset
    ? JSON.parse(JSON.stringify(asset.definition))
    : {
        name: "",
        role_id: "",
        role_version: 1,
        task: "",
        required_inputs: [],
        overrides: {},
      };
  inputNames.value = form.value.required_inputs?.join(", ") ?? "";
  contractText.value = form.value.output_contract
    ? JSON.stringify(form.value.output_contract, null, 2)
    : "";
  formError.value = "";
  editing.value = true;
}

function editGraph(asset: any) {
  editingAsset.value = asset;
  graphForm.value = asset
    ? JSON.parse(JSON.stringify(asset.definition))
    : {
        name: "",
        required_inputs: [],
        max_parallel: 4,
        max_depth: 3,
        tasks: [],
        edges: [],
      };
  graphInputNames.value = graphForm.value.required_inputs?.join(", ") ?? "";
  formError.value = "";
  graphInvalid.value = false;
  graphEditing.value = true;
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
    const definition = JSON.parse(JSON.stringify(graphForm.value));
    await workflowAssetsApi.save(editingAsset.value?.asset_id ?? null, {
      definition,
      expected_version: editingAsset.value?.version,
    });
    graphEditing.value = false;
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

function selectRole(id: string) {
  form.value.role_version =
    roles.value.find((role) => role.role_id === id)?.version ?? 1;
}

async function save() {
  if (saving.value || usageInvalid.value) return;
  saving.value = true;
  formError.value = "";
  try {
    form.value.required_inputs = inputNames.value
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean);
    if (contractText.value.trim())
      form.value.output_contract = JSON.parse(contractText.value);
    else delete form.value.output_contract;
    await workflowAssetsApi.save(editingAsset.value?.asset_id ?? null, {
      definition: form.value,
      expected_version: editingAsset.value?.version,
    });
    editing.value = false;
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

async function openRun(runId: string) {
  const read = ++runRead;
  openingRun = true;
  loading.value = true;
  try {
    error.value = "";
    const snapshot = await workflowAssetsApi.run(runId);
    if (disposed || read !== runRead) return;
    selectedRun.value = snapshot;
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
    if (!disposed && read === runRead) selectedRun.value = snapshot;
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
    if (!disposed && read === runRead) selectedRun.value = snapshot;
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
    if (!disposed && read === runRead) selectedRun.value = updated;
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
    if (!disposed && read === runRead) selectedRun.value = snapshot;
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
      previewUrl.value = previewText.value
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

<template>
  <section
    class="graph-workbench"
    :aria-label="tm(readonly ? 'v2.snapshot' : 'graph.canvas')"
  >
    <div class="graph-toolbar">
      <v-chip size="small" color="primary" variant="tonal">{{
        tm(readonly ? "v2.snapshot" : "graph.design")
      }}</v-chip>
      <span class="text-caption">{{
        tm("graph.count", { count: draft.tasks.length })
      }}</span>
      <v-spacer />
      <template v-if="!readonly">
        <v-select
          v-if="draft.edges?.length"
          v-model="selectedEdgeKey"
          :items="
            draft.edges.map((edge) => ({
              title: `${
                draft.tasks.find((task) => task.id === edge.source)?.name ??
                edge.source
              } → ${
                draft.tasks.find((task) => task.id === edge.target)?.name ??
                edge.target
              }`,
              value: `${edge.source}:${edge.target}`,
            }))
          "
          :label="tm('graph.dependency')"
          density="compact"
          hide-details
          class="graph-edge-picker"
          @update:model-value="selectedNode = null"
        />
        <v-btn
          variant="text"
          size="small"
          :disabled="historyIndex === 0"
          @click="restore(-1)"
          >{{ tm("graph.undo") }}</v-btn
        >
        <v-btn
          variant="text"
          size="small"
          :disabled="historyIndex === history.length - 1"
          @click="restore(1)"
          >{{ tm("graph.redo") }}</v-btn
        >
        <v-btn variant="tonal" size="small" @click="layout">{{
          tm("graph.layout")
        }}</v-btn>
      </template>
      <v-btn variant="text" size="small" @click="canvas?.fit()">{{
        tm("graph.fit")
      }}</v-btn>
      <v-btn
        variant="text"
        size="small"
        :aria-pressed="minimap"
        @click="minimap = !minimap"
        >{{ tm("graph.minimap") }}</v-btn
      >
    </div>
    <WorkflowLoopPanel
      :definition="draft"
      :readonly="readonly"
      :collapsed="collapsedLoops"
      :fields-for-task="fieldsForTask"
      :issues="issues"
      @update:loops="
        draft.loops = $event;
        checkpoint();
      "
      @toggle="toggleLoop"
    />
    <details
      v-for="task in draft.tasks.filter((item) => item.team)"
      :key="task.id"
      class="graph-child-preview ma-3 pa-3"
      data-testid="team-call-preview"
    >
      <summary class="text-wrap">
        {{ task.name }} →
        {{
          pinnedTeams[`${task.team!.asset_id}:${task.team!.version}`]
            ?.definition.name ?? task.team!.asset_id
        }}
        · v{{ task.team!.version }} · {{ tm("child.independent") }}
      </summary>
      <template
        v-if="pinnedTeams[`${task.team!.asset_id}:${task.team!.version}`]"
      >
        <p class="text-body-2 mt-3">{{ tm("child.contractHint") }}</p>
        <p class="text-body-2 mt-2">
          {{ tm("autonomous.coordinator") }}:
          {{
            pinnedTeams[`${task.team!.asset_id}:${task.team!.version}`]
              .definition.coordinator.role_id
          }}
          · v{{
            pinnedTeams[`${task.team!.asset_id}:${task.team!.version}`]
              .definition.coordinator.role_version
          }}
        </p>
        <p
          v-for="role in pinnedTeams[
            `${task.team!.asset_id}:${task.team!.version}`
          ].definition.allowed_roles"
          :key="role.role_id"
          class="text-body-2 mt-2"
        >
          {{ tm("autonomous.allowedRoles") }}: {{ role.role_id }} · v{{
            role.role_version
          }}
        </p>
      </template>
      <p v-else class="text-body-2 mt-3">{{ tm("child.unavailable") }}</p>
    </details>
    <details
      v-for="task in draft.tasks.filter((item) => item.workflow)"
      :key="task.id"
      class="graph-child-preview ma-3 pa-3"
      data-testid="workflow-call-preview"
    >
      <summary class="text-wrap">
        {{ task.name }} →
        {{
          pinnedTeams[`${task.workflow!.asset_id}:${task.workflow!.version}`]
            ?.definition.name ?? task.workflow!.asset_id
        }}
        · v{{ task.workflow!.version }} · {{ tm("child.independent") }}
      </summary>
      <template
        v-if="
          pinnedTeams[`${task.workflow!.asset_id}:${task.workflow!.version}`]
        "
      >
        <p class="text-body-2 mt-3">{{ tm("child.contractHint") }}</p>
        <p
          v-for="childTask in pinnedTeams[
            `${task.workflow!.asset_id}:${task.workflow!.version}`
          ].definition.tasks"
          :key="childTask.id"
          class="text-body-2 mt-2"
        >
          {{ childTask.name
          }}<span
            v-if="
              childTask.id ===
              pinnedTeams[
                `${task.workflow!.asset_id}:${task.workflow!.version}`
              ].definition.result_task_id
            "
          >
            · {{ tm("graph.outputs") }}</span
          >
        </p>
      </template>
      <p v-else class="text-body-2 mt-3">{{ tm("child.unavailable") }}</p>
    </details>
    <v-alert
      v-if="localError || Object.keys(contractErrors).length"
      type="error"
      variant="tonal"
      class="ma-3"
      role="alert"
      >{{ localError || Object.values(contractErrors)[0] }}</v-alert
    >
    <v-alert
      v-if="!readonly && !validResultTask"
      type="warning"
      variant="tonal"
      class="ma-3"
      data-testid="result-diagnostic"
    >
      {{ tm("graph.invalidResult") }}: {{ draft.result_task_id || "—" }}
      <v-btn variant="text" size="small" @click="selectNode('__output')">{{
        tm("graph.outputs")
      }}</v-btn>
    </v-alert>
    <v-alert
      v-for="issue in issues"
      :key="`${issue.path}:${issue.message}`"
      type="error"
      variant="tonal"
      class="ma-3"
    >
      {{ issue.path }}: {{ issue.message }}
    </v-alert>
    <div class="graph-columns" :class="{ 'is-readonly': readonly }">
      <details v-if="!readonly" class="graph-toolbox" :open="!narrow">
        <summary>{{ tm("graph.toolbox") }}</summary>
        <p class="text-caption my-3">{{ tm("graph.dragHint") }}</p>
        <v-btn
          variant="tonal"
          size="small"
          class="mb-3"
          data-testid="add-human-question"
          @click="addTask('human:question')"
          >{{ tm("human.questionNode") }}</v-btn
        >
        <v-btn
          variant="tonal"
          size="small"
          class="mb-3"
          data-testid="add-human-approval"
          @click="addTask('human:approval')"
          >{{ tm("human.approvalNode") }}</v-btn
        >
        <v-btn
          variant="tonal"
          size="small"
          class="mb-3"
          data-testid="add-condition"
          @click="addTask('control:condition')"
          >{{ tm("graph.conditionNode") }}</v-btn
        >
        <v-btn
          variant="tonal"
          size="small"
          class="mb-3"
          data-testid="add-join"
          @click="addTask('control:join')"
          >{{ tm("graph.joinNode") }}</v-btn
        >
        <v-btn
          v-for="team in teams"
          :key="team.asset_id"
          variant="tonal"
          size="small"
          class="graph-team-button mb-3"
          data-testid="add-team-call"
          @click="addTask(`team:${team.asset_id}`)"
          >{{ tm("child.call") }}: {{ team.definition.name }} · v{{
            team.version
          }}</v-btn
        >
        <v-btn
          v-for="workflow in workflows"
          :key="workflow.asset_id"
          variant="tonal"
          size="small"
          class="graph-team-button mb-3"
          data-testid="add-workflow-call"
          @click="addTask(`workflow:${workflow.asset_id}`)"
        >
          {{ tm("workflowCall.call") }}: {{ workflow.definition.name }} · v{{
            workflow.version
          }}
        </v-btn>
        <button
          v-for="role in roles"
          :key="role.role_id"
          class="graph-role"
          draggable="true"
          @dragstart="dragRole($event, role.role_id)"
          @click="addTask(role.role_id)"
        >
          <v-icon size="20" color="primary">mdi-account-outline</v-icon>
          <span
            ><strong>{{ role.definition.name }}</strong
            ><small>v{{ role.version }} · {{ tm("v2.role") }}</small></span
          >
          <v-icon size="16">mdi-plus</v-icon>
        </button>
        <p v-if="!roles.length" class="text-caption">
          {{ tm("graph.noRoles") }}
        </p>
      </details>
      <div class="graph-stage">
        <TeamsFlowCanvas
          ref="canvas"
          :nodes="nodes"
          :edges="edges"
          :mode="readonly ? 'monitor' : 'edit'"
          :minimap-visible="minimap"
          commit-positions-on-drag-stop
          :selected-edge-id="selectedEdgeKey"
          @drop-at="addTask"
          @connect="connect"
          @position-change="move"
          @select-node="selectNode"
          @select-edge="selectEdge"
          @delete-edge="removeEdge"
        />
        <div v-if="!draft.tasks.length" class="graph-empty">
          {{ tm("graph.empty") }}
        </div>
      </div>
      <details
        v-if="!readonly"
        class="graph-inspector"
        :open="!narrow || !!selectedNode || !!selectedEdge"
      >
        <summary>{{ tm("graph.properties") }}</summary>
        <template v-if="selectedTask">
          <div class="graph-inspector-heading">
            <v-icon color="primary">mdi-account-outline</v-icon
            ><strong>{{ selectedTask.name }}</strong
            ><code>{{ selectedTask.id }}</code>
          </div>
          <v-text-field
            v-model="selectedTask.name"
            :label="tm('graph.taskName')"
            density="compact"
            @update:model-value="checkpoint"
          />
          <v-select
            v-if="
              !selectedTask.control &&
              !selectedTask.human &&
              !selectedTask.team &&
              !selectedTask.workflow
            "
            :model-value="selectedTask.role ? 'inline' : 'template'"
            :items="[
              { title: tm('graph.directSource'), value: 'inline' },
              { title: tm('graph.templateSource'), value: 'template' },
            ]"
            :label="tm('graph.roleSource')"
            @update:model-value="changeSource"
          />
          <v-select
            v-if="
              !selectedTask.control &&
              !selectedTask.human &&
              !selectedTask.team &&
              !selectedTask.workflow &&
              !selectedTask.role
            "
            v-model="selectedTask.role_id"
            :items="roles"
            item-title="definition.name"
            item-value="role_id"
            :label="tm('v2.role')"
            density="compact"
            @update:model-value="changeRole"
          />
          <v-alert
            v-if="rolePreview?.deleted"
            type="warning"
            variant="tonal"
            class="mb-2"
            >{{ rolePreview.definition.name }} ·
            {{ tm("deletion.referenced") }}</v-alert
          >
          <v-select
            v-if="
              !selectedTask.control &&
              !selectedTask.human &&
              !selectedTask.team &&
              !selectedTask.workflow &&
              !selectedTask.role
            "
            v-model="selectedTask.role_version"
            :items="selectedRole?.versions ?? [selectedTask.role_version]"
            :label="tm('v2.roleVersion')"
            density="compact"
            @update:model-value="checkpoint"
          />
          <v-alert
            v-if="
              selectedTask.role_id &&
              selectedRole &&
              (selectedTask.role_version ?? 0) < selectedRole.version
            "
            type="info"
            variant="tonal"
            class="mb-3"
          >
            {{ tm("graph.newRoleVersion") }} v{{ selectedRole.version }}
            <v-btn
              variant="text"
              @click="
                selectedTask.role_version = selectedRole.version;
                checkpoint();
              "
              >{{ tm("graph.upgradeRole") }}</v-btn
            >
          </v-alert>
          <v-text-field
            v-if="
              !selectedTask.control &&
              !selectedTask.human &&
              !selectedTask.team &&
              !selectedTask.workflow
            "
            v-model="selectedTask.member"
            :label="tm('graph.memberKey')"
            :hint="tm('graph.memberHint')"
            persistent-hint
            density="compact"
            @update:model-value="checkpoint"
          />
          <v-textarea
            v-if="
              !selectedTask.control &&
              !selectedTask.human &&
              !selectedTask.team &&
              !selectedTask.workflow
            "
            v-model="selectedTask.task"
            :label="tm('v2.task')"
            :hint="tm('v2.taskHint')"
            persistent-hint
            rows="4"
            density="compact"
            class="mt-4"
            @update:model-value="checkpoint"
          />
          <template v-if="selectedTask.role && selectedTask.installation">
            <v-textarea
              data-testid="inline-responsibility"
              v-model="selectedTask.role.prompt"
              :label="tm('roles.prompt')"
              rows="3"
              @update:model-value="checkpoint"
            />
            <v-select
              v-model="selectedTask.installation.profile_id"
              :items="catalog.profiles"
              item-title="name"
              item-value="id"
              :label="tm('roles.profile')"
              @update:model-value="checkpoint"
            />
            <v-select
              v-model="selectedTask.role.provider_id"
              :items="catalog.models"
              item-title="name"
              item-value="id"
              :label="tm('graph.modelOverride')"
              clearable
              @update:model-value="checkpoint"
            />
            <p class="text-caption mb-3">
              {{ tm("graph.modelInheritanceHint") }}
            </p>
            <v-select
              v-model="selectedTask.role.persona_id"
              :items="catalog.personas"
              item-title="name"
              item-value="id"
              :label="tm('roles.localPersona')"
              clearable
              @update:model-value="checkpoint"
            />
            <v-expansion-panels multiple>
              <RoleResourcesEditor
                :key="selectedTask.id"
                :definition="selectedTask.role"
                :modes="resourceModes"
                :catalog="catalog"
                @change="checkpoint"
              />
            </v-expansion-panels>
            <v-btn
              variant="tonal"
              class="mt-3"
              :loading="savingRole"
              @click="saveInlineRole"
              >{{ tm("graph.saveRole") }}</v-btn
            >
            <v-alert v-if="roleMessage" variant="tonal" class="mt-3">{{
              roleMessage
            }}</v-alert>
          </template>
          <template v-else-if="selectedTask.role_id && selectedTask.overrides">
            <template
              v-for="field in ['prompt', 'provider_id', 'persona_id'] as const"
              :key="field"
            >
              <v-switch
                :data-testid="`override-${field}`"
                :model-value="
                  Object.prototype.hasOwnProperty.call(
                    selectedTask.overrides,
                    field,
                  )
                "
                :label="`${tm('roles.override')}: ${tm(
                  field === 'prompt'
                    ? 'roles.prompt'
                    : field === 'provider_id'
                    ? 'graph.modelOverride'
                    : 'roles.localPersona',
                )}`"
                @update:model-value="setOverride(field, $event)"
              />
              <v-textarea
                v-if="
                  field === 'prompt' &&
                  Object.prototype.hasOwnProperty.call(
                    selectedTask.overrides,
                    field,
                  )
                "
                v-model="selectedTask.overrides.prompt"
                data-testid="override-prompt-value"
                :label="tm('roles.prompt')"
                @update:model-value="checkpoint"
              />
              <v-select
                v-else-if="
                  field !== 'prompt' &&
                  Object.prototype.hasOwnProperty.call(
                    selectedTask.overrides,
                    field,
                  )
                "
                v-model="selectedTask.overrides[field]"
                :data-testid="`override-${field}-value`"
                :items="
                  field === 'provider_id' ? catalog.models : catalog.personas
                "
                item-title="name"
                item-value="id"
                :label="
                  tm(
                    field === 'provider_id'
                      ? 'graph.modelOverride'
                      : 'roles.localPersona',
                  )
                "
                clearable
                @update:model-value="checkpoint"
              />
            </template>
            <p class="text-caption">{{ tm("graph.overrideHint") }}</p>
            <v-expansion-panels multiple
              ><RoleResourcesEditor
                :key="selectedTask.id + ':overrides'"
                :definition="selectedTask.overrides"
                override
                :modes="resourceModes"
                :catalog="catalog"
                @change="checkpoint"
            /></v-expansion-panels>
            <div class="mt-4" data-testid="role-preview" aria-live="polite">
              <strong>{{ tm("graph.effectiveRole") }}</strong>
              <p class="text-caption">{{ tm("graph.previewUnsaved") }}</p>
              <p v-if="rolePreviewLoading">{{ tm("graph.previewLoading") }}</p>
              <v-alert
                v-else-if="rolePreviewError"
                type="error"
                variant="tonal"
                data-testid="role-preview-error"
                :data-issue-path="`definition.tasks.${draft.tasks.indexOf(
                  selectedTask,
                )}.role_id`"
                >{{ rolePreviewError }}</v-alert
              >
              <template v-else-if="rolePreview">
                <p
                  v-for="field in [
                    'prompt',
                    'persona_id',
                    'provider_id',
                    'tools',
                    'skills',
                    'knowledge_bases',
                    'human_wait',
                  ] as const"
                  :key="field"
                  :data-testid="`role-preview-${field}`"
                  class="text-body-2 my-2"
                >
                  <strong
                    >{{
                      tm(
                        field === "prompt"
                          ? "roles.prompt"
                          : field === "persona_id"
                          ? "roles.localPersona"
                          : field === "provider_id"
                          ? "graph.modelOverride"
                          : `roles.${field}`,
                      )
                    }}:</strong
                  >
                  {{ rolePreviewDisplay[field] }}
                  <span class="text-caption"
                    >({{
                      rolePreview.sources[field] === "task"
                        ? tm("graph.localOverride")
                        : rolePreview.sources[field] === "role"
                        ? `${tm("graph.templateSource")} v${
                            selectedTask.role_version
                          }`
                        : rolePreview.sources[field] ===
                          "workflow:default_provider_id"
                        ? tm("graph.workflowDefault")
                        : rolePreview.sources[field]
                    }})</span
                  >
                </p>
                <v-alert
                  v-for="(issue, index) in rolePreview.issues"
                  :key="index"
                  type="warning"
                  variant="tonal"
                  class="mb-2"
                  :data-issue-path="
                    issue.path === 'definition.default_provider_id'
                      ? issue.path
                      : `definition.tasks.${draft.tasks.indexOf(
                          selectedTask,
                        )}.${issue.path}`
                  "
                  >{{ issue.path }}: {{ issue.message }}</v-alert
                >
              </template>
            </div>
          </template>
          <template v-if="selectedTask.team">
            <v-alert
              v-if="
                pinnedTeams[
                  `${selectedTask.team.asset_id}:${selectedTask.team.version}`
                ]?.deleted
              "
              type="warning"
              variant="tonal"
              >{{
                pinnedTeams[
                  `${selectedTask.team.asset_id}:${selectedTask.team.version}`
                ].definition.name
              }}
              · {{ tm("deletion.referenced") }}</v-alert
            >
            <v-select
              :model-value="selectedTask.team.asset_id"
              :items="teams"
              item-title="definition.name"
              item-value="asset_id"
              :label="tm('child.asset')"
              data-testid="team-call-asset"
              @update:model-value="changeTeam($event)"
            />
            <v-select
              :model-value="selectedTask.team.version"
              :items="
                teams.find(
                  (team) => team.asset_id === selectedTask?.team?.asset_id,
                )?.versions ?? [selectedTask.team.version]
              "
              :label="tm('child.version')"
              data-testid="team-call-version"
              @update:model-value="
                changeTeam(selectedTask!.team!.asset_id, $event)
              "
            />
            <p class="text-caption my-3">{{ tm("child.contractHint") }}</p>
          </template>
          <template v-if="selectedTask.workflow">
            <v-alert
              v-if="
                pinnedTeams[
                  `${selectedTask.workflow.asset_id}:${selectedTask.workflow.version}`
                ]?.deleted
              "
              type="warning"
              variant="tonal"
              >{{
                pinnedTeams[
                  `${selectedTask.workflow.asset_id}:${selectedTask.workflow.version}`
                ].definition.name
              }}
              · {{ tm("deletion.referenced") }}</v-alert
            >
            <v-select
              :model-value="selectedTask.workflow.asset_id"
              :items="workflows"
              item-title="definition.name"
              item-value="asset_id"
              :label="tm('workflowCall.asset')"
              data-testid="workflow-call-asset"
              @update:model-value="changeTeam($event)"
            />
            <v-select
              :model-value="selectedTask.workflow.version"
              :items="
                workflows.find(
                  (item) => item.asset_id === selectedTask?.workflow?.asset_id,
                )?.versions ?? [selectedTask.workflow.version]
              "
              :label="tm('workflowCall.version')"
              data-testid="workflow-call-version"
              @update:model-value="
                changeTeam(selectedTask!.workflow!.asset_id, $event)
              "
            />
            <p class="text-caption my-3">{{ tm("child.contractHint") }}</p>
          </template>
          <template v-if="selectedTask.human">
            <v-textarea
              v-model="selectedTask.human.question"
              :label="tm('human.question')"
              data-testid="human-question"
              @update:model-value="checkpoint"
            />
            <HumanWaitPolicyEditor
              :model-value="selectedTask.human.human_wait"
              @update:model-value="
                selectedTask.human.human_wait = $event ?? {
                  timeout_seconds: null,
                  timeout_action: 'wait',
                };
                checkpoint();
              "
            />
            <template v-if="selectedTask.human.kind === 'approval'">
              <v-select
                v-model="selectedTask.human.artifact_task_id"
                :items="
                  draft.tasks
                    .filter(
                      (task) =>
                        task.id !== selectedTask?.id &&
                        !task.human &&
                        !task.control,
                    )
                    .map((task) => ({ title: task.name, value: task.id }))
                "
                :label="tm('human.artifactTask')"
                data-testid="human-artifact-task"
                @update:model-value="checkpoint"
              />
              <v-text-field
                v-model.number="selectedTask.human.artifact_version"
                type="number"
                min="1"
                :label="tm('v2.artifactVersion')"
                @update:model-value="checkpoint"
              />
              <p class="text-caption">{{ tm("human.pinHint") }}</p>
            </template>
            <template v-else>
              <v-textarea
                :model-value="(selectedTask.human.choices ?? []).join('\n')"
                :label="tm('human.choices')"
                @update:model-value="
                  selectedTask.human.choices = $event
                    .split('\n')
                    .map((value: string) => value.trim())
                    .filter(Boolean);
                  checkpoint();
                "
              />
            </template>
            <v-select
              v-model="selectedTask.human.recommendation_task_id"
              clearable
              :items="
                draft.tasks
                  .filter(
                    (task) =>
                      task.id !== selectedTask?.id &&
                      !task.human &&
                      !task.control &&
                      !task.team &&
                      !task.workflow,
                  )
                  .map((task) => ({ title: task.name, value: task.id }))
              "
              :label="tm('human.recommendationTask')"
              @update:model-value="
                !$event &&
                  ((selectedTask.human.recommendation_value_field = null),
                  (selectedTask.human.recommendation_reason_field = null));
                checkpoint();
              "
            />
            <template v-if="selectedTask.human.recommendation_task_id">
              <v-text-field
                v-model="selectedTask.human.recommendation_value_field"
                :label="tm('human.recommendationValueField')"
                @update:model-value="checkpoint"
              />
              <v-text-field
                v-model="selectedTask.human.recommendation_reason_field"
                :label="tm('human.recommendationReasonField')"
                @update:model-value="checkpoint"
              />
            </template>
            <template v-if="selectedTask.human.kind === 'question'">
              <v-text-field
                v-model.number="selectedTask.human.max_length"
                type="number"
                min="1"
                :label="tm('human.maxLength')"
                @update:model-value="checkpoint"
              />
            </template>
          </template>
          <template v-else-if="selectedTask.control?.kind !== 'join'">
            <h3 class="text-subtitle-2 mt-4 mb-2">{{ tm("graph.inputs") }}</h3>
            <div
              v-for="(ref, alias) in selectedTask.inputs"
              :key="inputRenderKeys[`${selectedTask.id}:${alias}`] ?? alias"
              class="graph-input"
            >
              <v-text-field
                v-if="!selectedTask.control"
                :model-value="
                  aliasTexts[`${selectedTask.id}:${alias}`] ?? alias
                "
                :label="tm('graph.inputAlias')"
                :readonly="!!selectedTask.team || !!selectedTask.workflow"
                density="compact"
                @update:model-value="
                  aliasTexts[`${selectedTask.id}:${alias}`] = $event
                "
                @update:focused="
                  !$event &&
                    renameInput(
                      String(alias),
                      aliasTexts[`${selectedTask.id}:${alias}`] ??
                        String(alias),
                    )
                "
              />
              <p
                v-if="ref.task_id && !ancestors.has(ref.task_id)"
                class="text-error text-caption"
              >
                {{ tm("graph.invalidSource") }}: {{ ref.task_id }}
              </p>
              <p
                v-if="ref.input && !draft.required_inputs?.includes(ref.input)"
                class="text-error text-caption"
              >
                {{ tm("graph.invalidSource") }}: {{ ref.input }}
              </p>
              <v-select
                :model-value="ref.input ? `input:${ref.input}` : ref.task_id"
                :items="inputSources"
                :label="tm('graph.source')"
                data-testid="input-source"
                density="compact"
                @update:model-value="setInputSource(String(alias), $event)"
              />
              <p
                v-if="
                  ref.task_id &&
                  !fieldsForTask(ref.task_id).some(
                    (item) => item.value === ref.field,
                  )
                "
                class="text-error text-caption"
              >
                {{ tm("graph.invalidField") }}: {{ ref.field }}
              </p>
              <v-select
                v-if="ref.task_id"
                v-model="ref.field"
                :items="fieldsForTask(ref.task_id)"
                :label="tm('graph.field')"
                data-testid="input-field"
                :hint="tm('graph.fieldHint')"
                persistent-hint
                density="compact"
                @update:model-value="resetControlValue"
              />
              <v-btn
                v-if="
                  !selectedTask.control &&
                  !selectedTask.team &&
                  !selectedTask.workflow
                "
                variant="text"
                size="small"
                color="error"
                @click="
                  delete selectedTask.inputs?.[alias];
                  checkpoint();
                "
                >{{ tm("graph.removeInput") }}</v-btn
              >
            </div>
            <v-btn
              v-if="
                !selectedTask.control &&
                !selectedTask.team &&
                !selectedTask.workflow
              "
              variant="tonal"
              size="small"
              @click="addInput"
              >{{ tm("graph.addInput") }}</v-btn
            >
            <template v-if="selectedTask.control?.kind === 'condition'">
              <v-select
                v-model="selectedTask.control.operator"
                :items="operatorsForControl"
                :label="tm('graph.operator')"
                data-testid="control-operator"
                @update:model-value="checkpoint"
              />
              <v-select
                v-if="controlValueType === 'null'"
                :model-value="null"
                :items="[{ title: 'null', value: null }]"
                :label="tm('graph.value')"
                disabled
              />
              <v-select
                v-else-if="controlValueType === 'boolean'"
                :model-value="selectedTask.control.value"
                :items="[
                  { title: tm('graph.trueExit'), value: true },
                  { title: tm('graph.falseExit'), value: false },
                ]"
                :label="tm('graph.value')"
                data-testid="control-value-boolean"
                @update:model-value="setControlValue"
              />
              <v-text-field
                v-else
                :model-value="selectedTask.control.value"
                :type="
                  controlValueType === 'number' ||
                  controlValueType === 'integer'
                    ? 'number'
                    : 'text'
                "
                :label="tm('graph.value')"
                data-testid="control-value"
                @update:model-value="setControlValue"
              />
            </template>
            <template v-else>
              <p class="text-caption my-2">{{ tm("graph.referenceHint") }}</p>
              <h3 class="text-subtitle-2 mt-4 mb-2">
                {{ tm("graph.outputs") }}
              </h3>
              <pre
                v-if="selectedTask.team || selectedTask.workflow"
                class="text-caption child-output-contract"
                data-testid="child-output-contract"
                >{{
                  JSON.stringify(
                    selectedTask.output_contract ?? {
                      type: "object",
                      properties: { text: { type: "string" } },
                      required: ["text"],
                    },
                    null,
                    2,
                  )
                }}</pre
              >
              <v-switch
                :model-value="!!selectedTask.output_contract"
                :label="tm('graph.structured')"
                data-testid="structured-output"
                :disabled="!!selectedTask.team || !!selectedTask.workflow"
                @update:model-value="setStructured"
              />
              <p
                v-if="
                  selectedTask.output_contract &&
                  !selectedTask.team &&
                  !selectedTask.workflow &&
                  !editableContract
                "
                class="text-caption"
              >
                {{ tm("graph.complexContract") }}
              </p>
              <template
                v-if="
                  selectedTask.output_contract &&
                  !selectedTask.team &&
                  !selectedTask.workflow &&
                  editableContract
                "
              >
                <p class="text-caption">{{ tm("graph.structuredHint") }}</p>
                <div
                  v-for="(schema, name) in selectedTask.output_contract
                    .properties ?? {}"
                  :key="name"
                  class="graph-input"
                >
                  <v-text-field
                    :model-value="name"
                    :label="tm('graph.field')"
                    density="compact"
                    @change="
                      renameOutput(
                        String(name),
                        ($event.target as HTMLInputElement).value,
                      )
                    "
                  />
                  <v-select
                    :model-value="(schema as any).type"
                    :items="['string', 'integer', 'number', 'boolean']"
                    :label="tm('graph.fieldType')"
                    data-testid="output-field-type"
                    density="compact"
                    @update:model-value="setOutputType(String(name), $event)"
                  />
                  <v-btn
                    variant="text"
                    size="small"
                    color="error"
                    @click="removeOutput(String(name))"
                    >{{ tm("graph.removeInput") }}</v-btn
                  >
                </div>
                <v-btn variant="tonal" size="small" @click="addOutput">{{
                  tm("graph.addField")
                }}</v-btn>
              </template>
            </template>
          </template>
          <p v-else class="text-caption">{{ tm("graph.joinHint") }}</p>
          <v-btn
            variant="text"
            color="error"
            class="mt-3"
            @click="removeTask"
            >{{ tm("graph.removeTask") }}</v-btn
          >
        </template>
        <template v-else-if="selectedNode === '__input'">
          <v-text-field
            :model-value="(draft.required_inputs ?? []).join(', ')"
            :label="tm('v2.inputNames')"
            :hint="tm('v2.inputHint')"
            persistent-hint
            @update:model-value="setWorkflowInputs"
          />
        </template>
        <template v-else-if="selectedNode === '__output'">
          <p class="text-body-2 my-4">{{ tm("graph.outputHint") }}</p>
          <v-select
            v-model="draft.result_task_id"
            :items="
              draft.tasks
                .filter((task) => !task.control)
                .map((task) => ({ title: task.name, value: task.id }))
            "
            :label="tm('graph.resultTask')"
            data-testid="result-task"
            @update:model-value="checkpoint"
          />
        </template>
        <template v-else-if="selectedEdge">
          <p class="text-subtitle-2 my-3">
            {{
              draft.tasks.find((task) => task.id === selectedEdge?.source)?.name
            }}
            →
            {{
              draft.tasks.find((task) => task.id === selectedEdge?.target)?.name
            }}
          </p>
          <v-select
            v-if="edgeSourceControl || edgeSourceApproval"
            :model-value="selectedEdge.condition?.value"
            :items="[
              {
                title: tm(
                  edgeSourceApproval ? 'human.approve' : 'graph.trueExit',
                ),
                value: true,
              },
              {
                title: tm(
                  edgeSourceApproval
                    ? 'human.returnForRevision'
                    : 'graph.falseExit',
                ),
                value: false,
              },
            ]"
            :label="tm('graph.exit')"
            data-testid="edge-exit"
            @update:model-value="setEdgeExit"
          />
          <v-switch
            v-else
            :model-value="!!selectedEdge.condition"
            :label="tm('graph.condition')"
            color="primary"
            density="compact"
            @update:model-value="toggleCondition"
          />
          <template
            v-if="
              selectedEdge.condition &&
              !edgeSourceControl &&
              !edgeSourceApproval
            "
          >
            <v-text-field
              v-model="selectedEdge.condition.field"
              :label="tm('graph.field')"
              density="compact"
              @update:model-value="checkpoint"
            />
            <v-select
              v-model="selectedEdge.condition.operator"
              :items="operatorItems"
              :label="tm('graph.operator')"
              density="compact"
              @update:model-value="checkpoint"
            />
            <v-text-field
              :model-value="
                typeof selectedEdge.condition.value === 'string'
                  ? selectedEdge.condition.value
                  : JSON.stringify(selectedEdge.condition.value)
              "
              :label="tm('graph.value')"
              :hint="tm('graph.valueHint')"
              persistent-hint
              density="compact"
              @update:model-value="setConditionValue"
            />
          </template>
          <p class="text-caption my-3">{{ tm("graph.conditionHint") }}</p>
          <v-btn
            variant="text"
            color="error"
            @click="removeEdge(selectedEdgeKey!)"
            >{{ tm("graph.removeEdge") }}</v-btn
          >
        </template>
        <p v-else class="text-body-2 my-4">{{ tm("graph.selectHint") }}</p>
      </details>
    </div>
    <div class="graph-footer text-caption">
      {{ tm(readonly ? "graph.runHint" : "graph.footer") }}
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import TeamsFlowCanvas from "./TeamsFlowCanvas.vue";
import WorkflowLoopPanel from "./WorkflowLoopPanel.vue";
import RoleResourcesEditor from "./RoleResourcesEditor.vue";
import HumanWaitPolicyEditor from "./HumanWaitPolicyEditor.vue";
import type { FlowNode } from "./TeamsFlowCanvas.vue";
import type { GraphDefinition } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";
import { collabMemberColor } from "@/utils/memberColors";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { roleAssetsApi } from "@/api/roleAssets";
import { extractApiError } from "@/utils/extractApiError";

const props = withDefaults(
  defineProps<{
    definition: GraphDefinition;
    roles?: any[];
    catalog?: Record<string, any[]>;
    issues?: { path: string; message: string }[];
    teams?: any[];
    workflows?: any[];
    childAssets?: Record<string, any>;
    readonly?: boolean;
    tasks?: any[];
    dispatchTasks?: string[];
  }>(),
  {
    roles: () => [],
    catalog: () => ({
      profiles: [],
      models: [],
      personas: [],
      tools: [],
      skills: [],
      knowledge_bases: [],
    }),
    issues: () => [],
    teams: () => [],
    workflows: () => [],
    childAssets: () => ({}),
    readonly: false,
    tasks: () => [],
    dispatchTasks: () => [],
  },
);
const emit = defineEmits<{
  (event: "update:definition", definition: GraphDefinition): void;
  (event: "selectTask", nodeId: string | null): void;
  (event: "validation", invalid: boolean): void;
  (event: "roleSaved"): void;
}>();
const { tm } = useModuleI18n("features/agent-teams");
const draft = ref<GraphDefinition>(
  JSON.parse(JSON.stringify(props.definition)),
);
const canvas = ref<InstanceType<typeof TeamsFlowCanvas>>();
const selectedNode = ref<string | null>(
  props.definition.tasks.length === 1 && props.definition.tasks[0].role
    ? props.definition.tasks[0].id
    : null,
);
watch(
  () => props.issues,
  (issues) => {
    const match = issues
      .map((issue) => /^definition\.tasks\.(\d+)/.exec(issue.path))
      .find(Boolean);
    if (match)
      selectedNode.value = draft.value.tasks[Number(match[1])]?.id ?? null;
    else if (issues.some((issue) => issue.path === "definition.result_task_id"))
      selectedNode.value = "__output";
  },
);
const selectedEdgeKey = ref<string | null>(null);
const minimap = ref(props.definition.tasks.length !== 1);
const collapsedLoops = ref<string[]>([]);
const localError = ref("");
const contractErrors = ref<Record<string, string>>({});
const contractTexts = ref<Record<string, string>>({});
const aliasTexts = ref<Record<string, string>>({});
const inputRenderKeys = ref<Record<string, string>>({});
const pinnedTeams = ref<Record<string, any>>({});
const teamReadError = ref(false);
const selectingTeam = ref(false);
const readingTeams = ref(false);
let teamSelectionRequest = 0;
let teamReadRequest = 0;
const widthQuery = window.matchMedia?.("(max-width: 1000px)");
const narrow = ref(widthQuery?.matches ?? false);
const updateWidth = (event: MediaQueryListEvent) => {
  narrow.value = event.matches;
};
widthQuery?.addEventListener?.("change", updateWidth);
const history = ref<string[]>([JSON.stringify(draft.value)]);
const historyIndex = ref(0);
const selectedTask = computed(() =>
  draft.value.tasks.find((task) => task.id === selectedNode.value),
);
const resourceModes = ref<Record<string, string>>({});
const savingRole = ref(false);
const roleMessage = ref("");
const rolePreview = ref<any>(null);
const rolePreviewDisplay = computed<Record<string, string>>(() => {
  if (!rolePreview.value) return {};
  const effective = rolePreview.value.effective;
  const display: Record<string, string> = {
    prompt: effective.prompt || tm("graph.emptyResponsibility"),
  };
  for (const [field, category] of [
    ["persona_id", "personas"],
    ["provider_id", "models"],
    ["tools", "tools"],
    ["skills", "skills"],
    ["knowledge_bases", "knowledge_bases"],
  ]) {
    const value = effective[field];
    const ids = Array.isArray(value) ? value : value ? [value] : [];
    display[field] = ids.length
      ? ids
          .map((id: string) => {
            const name = props.catalog[category]?.find((item) => item.id === id)
              ?.name;
            return name && name !== id ? `${name} (${id})` : id;
          })
          .join(", ")
      : Array.isArray(value)
      ? tm("graph.resourcesDisabled")
      : field === "persona_id"
      ? tm("graph.noPersona")
      : tm("graph.noModel");
  }
  const policy = effective.human_wait;
  display.human_wait =
    policy?.timeout_seconds == null
      ? tm("humanWait.unlimited")
      : `${policy.timeout_seconds} ${tm("humanWait.seconds")} · ${tm(
          policy.timeout_action === "wait"
            ? "humanWait.wait"
            : "humanWait.recommendation",
        )}`;
  return display;
});
const rolePreviewError = ref("");
const rolePreviewLoading = ref(false);
let rolePreviewRequest = 0;
watch(
  () =>
    JSON.stringify({
      id: selectedTask.value?.id,
      role: selectedTask.value?.role_id,
      version: selectedTask.value?.role_version,
      overrides: selectedTask.value?.overrides,
      defaultProvider: draft.value.default_provider_id,
    }),
  async () => {
    const request = ++rolePreviewRequest;
    rolePreview.value = null;
    rolePreviewError.value = "";
    const task = selectedTask.value;
    if (!task?.role_id || !task.role_version || props.readonly) {
      rolePreviewLoading.value = false;
      return;
    }
    rolePreviewLoading.value = true;
    const roleId = task.role_id;
    const version = task.role_version;
    const overrides = JSON.parse(JSON.stringify(task.overrides ?? {}));
    const defaultProvider = draft.value.default_provider_id;
    try {
      let preview = await roleAssetsApi.resolve(roleId, version, overrides);
      if (request !== rolePreviewRequest) return;
      if (
        defaultProvider &&
        preview.effective.runner.type === "local" &&
        !preview.effective.provider_id
      ) {
        preview = await roleAssetsApi.resolve(roleId, version, {
          ...overrides,
          provider_id: defaultProvider,
        });
        if (request !== rolePreviewRequest) return;
        preview.sources.provider_id = "workflow:default_provider_id";
        for (const issue of preview.issues)
          if (issue.path === "overrides.provider_id")
            issue.path = "definition.default_provider_id";
      }
      rolePreview.value = preview;
    } catch (error: any) {
      if (request !== rolePreviewRequest) return;
      const failure = extractApiError(error, tm("errors.operationFailed"));
      rolePreviewError.value = [
        failure.message,
        ...failure.fields.map((field) => `${field.path}: ${field.message}`),
      ].join(" ");
    } finally {
      if (request === rolePreviewRequest) rolePreviewLoading.value = false;
    }
  },
  { immediate: true, flush: "sync" },
);
watch(
  selectedTask,
  (task) => {
    roleMessage.value = "";
    if (task?.role_id && !task.overrides) task.overrides = {};
    for (const field of ["tools", "skills", "knowledge_bases"])
      resourceModes.value[field] =
        ((task?.role ?? task?.overrides) as any)?.[field] == null
          ? "inherit"
          : "custom";
  },
  { immediate: true },
);
watch(
  [selectedTask, () => props.readonly],
  () => {
    teamSelectionRequest++;
    if (!selectingTeam.value) return;
    selectingTeam.value = false;
    emit(
      "validation",
      readingTeams.value ||
        teamReadError.value ||
        !!Object.keys(contractErrors.value).length,
    );
  },
  { flush: "sync" },
);
onBeforeUnmount(() => {
  widthQuery?.removeEventListener?.("change", updateWidth);
  teamSelectionRequest++;
  teamReadRequest++;
  rolePreviewRequest++;
});
const selectedEdge = computed(
  () =>
    draft.value.edges?.find(
      (edge) => `${edge.source}:${edge.target}` === selectedEdgeKey.value,
    ),
);
const selectedRole = computed(() =>
  props.roles.find((role) => role.role_id === selectedTask.value?.role_id),
);
const ancestors = computed(() => {
  const found = new Set<string>();
  const pending = [selectedNode.value];
  while (pending.length) {
    const current = pending.pop();
    for (const edge of draft.value.edges ?? []) {
      if (edge.target === current && !found.has(edge.source)) {
        found.add(edge.source);
        pending.push(edge.source);
      }
    }
  }
  return found;
});
const editableContract = computed(() => {
  const schema = selectedTask.value?.output_contract as any;
  if (
    !schema ||
    schema.type !== "object" ||
    !schema.properties ||
    !Array.isArray(schema.required)
  )
    return false;
  if (
    Object.keys(schema).some(
      (key) =>
        !["type", "properties", "required", "additionalProperties"].includes(
          key,
        ),
    )
  )
    return false;
  return Object.entries(schema.properties).every(
    ([name, value]: [string, any]) =>
      schema.required.includes(name) &&
      Object.keys(value).length === 1 &&
      ["string", "integer", "number", "boolean", "null"].includes(value.type),
  );
});
const inputSources = computed(() => [
  ...(draft.value.required_inputs ?? []).map((name) => ({
    title: `${tm("graph.workflowInput")}: ${name}`,
    value: `input:${name}`,
  })),
  ...draft.value.tasks
    .filter((task) => ancestors.value.has(task.id) && !task.control)
    .map((task) => ({ title: task.name, value: task.id })),
]);
function fieldsForTask(id: string) {
  const task = draft.value.tasks.find((item) => item.id === id);
  if (!task) return [];
  if (!task.output_contract) return [{ title: "text · string", value: "text" }];
  const fields: { title: string; value: string }[] = [];
  const visit = (schema: any, prefix = "") => {
    if (
      schema?.type !== "object" ||
      !schema.properties ||
      !Array.isArray(schema.required)
    )
      return;
    for (const name of schema.required) {
      const field = schema.properties[name];
      const path = prefix ? `${prefix}.${name}` : name;
      if (
        ["string", "integer", "number", "boolean", "null"].includes(field?.type)
      )
        fields.push({ title: `${path} · ${field.type}`, value: path });
      else if (field?.type === "object") visit(field, path);
    }
  };
  visit(task.output_contract);
  return fields;
}
function setStructured(enabled: boolean | null) {
  if (!selectedTask.value) return;
  selectedTask.value.output_contract = enabled
    ? {
        type: "object",
        properties: {},
        required: [],
        additionalProperties: false,
      }
    : null;
  checkpoint();
}
function addOutput() {
  const schema = selectedTask.value?.output_contract as any;
  if (!schema) return;
  let i = 1;
  while (`field${i}` in schema.properties) i++;
  const name = `field${i}`;
  schema.properties[name] = { type: "string" };
  schema.required.push(name);
  checkpoint();
}
function renameOutput(oldName: string, name: string) {
  const schema = selectedTask.value?.output_contract as any;
  if (!schema || name === oldName) return;
  if (!/^[a-zA-Z_]\w*$/.test(name) || name in schema.properties) {
    localError.value = tm("graph.invalidAlias");
    return;
  }
  schema.properties[name] = schema.properties[oldName];
  delete schema.properties[oldName];
  schema.required = schema.required.map((item: string) =>
    item === oldName ? name : item,
  );
  checkpoint();
}
function setOutputType(name: string, type: string) {
  const schema = selectedTask.value?.output_contract as any;
  if (!schema) return;
  schema.properties[name] = { type };
  checkpoint();
}
function removeOutput(name: string) {
  const schema = selectedTask.value?.output_contract as any;
  if (!schema) return;
  delete schema.properties[name];
  schema.required = schema.required.filter((item: string) => item !== name);
  checkpoint();
}
const operators = ["eq", "ne", "gt", "ge", "lt", "le"];
const edgeSourceControl = computed(
  () =>
    draft.value.tasks.find((task) => task.id === selectedEdge.value?.source)
      ?.control?.kind === "condition",
);
const edgeSourceApproval = computed(
  () =>
    draft.value.tasks.find((task) => task.id === selectedEdge.value?.source)
      ?.human?.kind === "approval",
);
const controlValueType = computed(() => {
  const ref = selectedTask.value?.inputs?.value;
  if (!ref || ref.input != null) return "string";
  const field = fieldsForTask(ref.task_id ?? "").find(
    (item) => item.value === ref.field,
  );
  return field?.title.split(" · ").pop() ?? "string";
});
const operatorItems = computed(() =>
  operators.map((value) => ({ title: tm(`graph.operators.${value}`), value })),
);
const operatorsForControl = computed(() =>
  ["integer", "number"].includes(controlValueType.value)
    ? operatorItems.value
    : operatorItems.value.filter((item) => ["eq", "ne"].includes(item.value)),
);
function resetControlValue() {
  const control = selectedTask.value?.control;
  if (control?.kind === "condition") {
    control.operator = "eq";
    control.value =
      controlValueType.value === "boolean"
        ? false
        : controlValueType.value === "null"
        ? null
        : ["integer", "number"].includes(controlValueType.value)
        ? 0
        : "";
  }
  checkpoint();
}
function setControlValue(value: any) {
  const control = selectedTask.value?.control;
  if (!control || control.kind !== "condition") return;
  control.value = ["integer", "number"].includes(controlValueType.value)
    ? Number(value)
    : value;
  checkpoint();
}
function setEdgeExit(value: string | number | boolean) {
  if (!selectedEdge.value || typeof value !== "boolean") return;
  selectedEdge.value.condition = {
    field: edgeSourceApproval.value ? "approved" : "matched",
    operator: "eq",
    value,
  };
  checkpoint();
}
const taskNodes = computed<FlowNode[]>(() =>
  draft.value.tasks.map((task, index) => {
    const running = props.tasks.find((item) => item.node_id === task.id);
    const role = props.roles.find((item) => item.role_id === task.role_id);
    const state = running?.status;
    return {
      id: task.id,
      position: {
        x: task.position?.x ?? (index % 3) * 300,
        y: task.position?.y ?? Math.floor(index / 3) * 220,
      },
      data: {
        label: task.name,
        controlKind: task.control?.kind,
        approval: task.human?.kind === "approval",
        trueLabel: tm(
          task.human?.kind === "approval" ? "human.approve" : "graph.trueExit",
        ),
        falseLabel: tm(
          task.human?.kind === "approval"
            ? "human.returnForRevision"
            : "graph.falseExit",
        ),
        memberName: task.control
          ? tm(
              task.control.kind === "condition"
                ? "graph.conditionNode"
                : "graph.joinNode",
            )
          : task.workflow
          ? tm("workflowCall.call")
          : task.team
          ? tm("child.independent")
          : task.human
          ? tm(`human.${task.human.kind}Node`)
          : `${
              task.role?.name ??
              role?.definition.name ??
              running?.snapshot.member?.name ??
              task.role_id
            } · ${task.member}`,
        memberColor: collabMemberColor(task.member ?? task.id),
        taskPreview: task.control
          ? task.control.kind === "condition"
            ? `${tm(
                `graph.operators.${task.control.operator ?? "eq"}`,
              )} ${JSON.stringify(task.control.value)}`
            : tm("graph.joinHint")
          : task.workflow
          ? `${task.workflow.asset_id} → ${tm("graph.outputs")}`
          : task.team
          ? pinnedTeams.value[`${task.team.asset_id}:${task.team.version}`]
              ?.definition.name ?? task.team.asset_id
          : task.human?.question ?? task.task,
        nodeNumber: index + 1,
        configLabel: task.control
          ? ""
          : task.workflow
          ? `v${task.workflow.version}`
          : task.team
          ? `v${task.team.version}`
          : task.human
          ? task.human.kind === "approval"
            ? `v${task.human.artifact_version ?? 1}`
            : ""
          : task.role
          ? tm("graph.inlineAgent")
          : `v${task.role_version}`,
        inputLabel: `${tm("graph.inputs")} · ${
          Object.keys(task.inputs ?? {}).length
        }`,
        outputLabel: task.control
          ? task.control.kind === "condition"
            ? `${tm("graph.trueExit")} / ${tm("graph.falseExit")}`
            : "ALL"
          : task.workflow || task.team
          ? fieldsForTask(task.id)
              .map((field) => field.title)
              .join(", ")
          : task.output_contract
          ? tm("graph.structured")
          : tm("graph.text"),
        status:
          state === "completed"
            ? "done"
            : ["blocked", "dependency_failed", "awaiting_result"].includes(
                state,
              )
            ? "failed"
            : state,
        statusLabel:
          state === "pending"
            ? tm(
                running?.state.wait_kind === "predecessors"
                  ? "graph.waitingPredecessors"
                  : running?.state.wait_kind === "capacity"
                  ? "graph.waitingCapacity"
                  : "graph.notStarted",
              )
            : state
            ? tm(`v2.states.${state}`)
            : "",
        error: running?.state.reason ?? "",
        inDegree:
          draft.value.edges?.filter((edge) => edge.target === task.id).length ??
          0,
      },
    };
  }),
);
const validResultTask = computed(() =>
  draft.value.tasks.some(
    (task) => task.id === draft.value.result_task_id && !task.control,
  ),
);
const showInterfaceNodes = computed(
  () => !props.readonly || validResultTask.value,
);
const collapsedGroups = computed(() =>
  (draft.value.loops ?? []).filter((loop) =>
    collapsedLoops.value.includes(loop.id),
  ),
);
const nodes = computed<FlowNode[]>(() => [
  ...(showInterfaceNodes.value
    ? [
        {
          id: "__input",
          position: {
            x:
              Math.min(0, ...taskNodes.value.map((node) => node.position.x)) -
              300,
            y: 0,
          },
          data: {
            label: tm("graph.workflowInput"),
            memberName: (draft.value.required_inputs ?? []).join(", "),
            outputLabel: tm("graph.inputs"),
          },
        },
        {
          id: "__output",
          position: {
            x:
              Math.max(0, ...taskNodes.value.map((node) => node.position.x)) +
              300,
            y: 0,
          },
          data: {
            label: tm("graph.outputs"),
            memberName: draft.value.tasks.find(
              (task) => task.id === draft.value.result_task_id,
            )?.output_contract
              ? tm("graph.structured")
              : tm("graph.text"),
            inputLabel:
              draft.value.tasks.find(
                (task) => task.id === draft.value.result_task_id,
              )?.name ??
              draft.value.result_task_id ??
              "",
          },
        },
      ]
    : []),
  ...taskNodes.value.filter(
    (node) =>
      !collapsedGroups.value.some((loop) => loop.body.includes(node.id)),
  ),
  ...collapsedGroups.value.map((loop) => ({
    id: `loop:${loop.id}`,
    position: taskNodes.value.find((node) => loop.body.includes(node.id))
      ?.position ?? { x: 0, y: 0 },
    data: {
      label: loop.name,
      memberName: tm("loops.group"),
      taskPreview: `${loop.exit_task_id}.${loop.exit_condition.field} ${
        loop.exit_condition.operator ?? "eq"
      } ${JSON.stringify(loop.exit_condition.value)}`,
      configLabel: `${tm("loops.maximum")} ${loop.max_iterations}`,
      inputLabel: `${loop.body.length} ${tm("loops.tasks")}`,
      outputLabel: tm("loops.expand"),
    },
  })),
]);
const edges = computed(() =>
  (
    [
      ...(showInterfaceNodes.value && draft.value.tasks.length === 1
        ? [
            { source: "__input", target: draft.value.tasks[0].id },
            ...(validResultTask.value
              ? [{ source: draft.value.result_task_id!, target: "__output" }]
              : []),
          ]
        : []),
      ...(showInterfaceNodes.value &&
      draft.value.tasks.length !== 1 &&
      validResultTask.value
        ? [{ source: draft.value.result_task_id!, target: "__output" }]
        : []),
      ...(draft.value.edges ?? []),
    ] as NonNullable<GraphDefinition["edges"]>
  )
    .map((edge) => {
      const sourceGroup = collapsedGroups.value.find((loop) =>
        loop.body.includes(edge.source),
      );
      const targetGroup = collapsedGroups.value.find((loop) =>
        loop.body.includes(edge.target),
      );
      return {
        id: `${edge.source}:${edge.target}`,
        source: sourceGroup ? `loop:${sourceGroup.id}` : edge.source,
        target: targetGroup ? `loop:${targetGroup.id}` : edge.target,
        sourceHandle:
          (draft.value.tasks.find((task) => task.id === edge.source)?.control
            ?.kind === "condition" ||
            draft.value.tasks.find((task) => task.id === edge.source)?.human
              ?.kind === "approval") &&
          edge.condition
            ? `matched-${edge.condition.value}`
            : undefined,
        label:
          (draft.value.tasks.find((task) => task.id === edge.source)?.control
            ?.kind === "condition" ||
            draft.value.tasks.find((task) => task.id === edge.source)?.human
              ?.kind === "approval") &&
          edge.condition
            ? tm(
                draft.value.tasks.find((task) => task.id === edge.source)?.human
                  ?.kind === "approval"
                  ? edge.condition.value
                    ? "human.approve"
                    : "human.returnForRevision"
                  : edge.condition.value
                  ? "graph.trueExit"
                  : "graph.falseExit",
              )
            : edge.condition
            ? `${edge.condition.field} ${
                edge.condition.operator
              } ${JSON.stringify(edge.condition.value)}`
            : props.dispatchTasks.includes(edge.target) &&
              edge.source === "coordinator"
            ? tm("autonomous.dispatched")
            : undefined,
        style:
          props.dispatchTasks.includes(edge.target) &&
          edge.source === "coordinator"
            ? { strokeDasharray: "5 5" }
            : undefined,
      };
    })
    .filter((edge) => edge.source !== edge.target),
);

watch(
  () => [
    draft.value.tasks.map((task) => task.team ?? task.workflow),
    props.teams,
    props.workflows,
    props.childAssets,
  ],
  async () => {
    const request = ++teamReadRequest;
    const definition = draft.value;
    /** Check whether a definition read still belongs to the current canvas.
     * Returns:
     *   Whether this read can publish its cache and validation state.
     */
    const isCurrent = () =>
      request === teamReadRequest && draft.value === definition;
    readingTeams.value = true;
    teamReadError.value = false;
    emit("validation", true);
    for (const task of definition.tasks) {
      const call = task.team ?? task.workflow;
      if (!call) continue;
      const { asset_id, version } = call;
      const key = `${asset_id}:${version}`;
      if (pinnedTeams.value[key]) continue;
      const frozen = props.childAssets[task.id];
      const listed = [...props.teams, ...props.workflows].find(
        (team) => team.asset_id === asset_id && team.version === version,
      );
      try {
        const team = frozen
          ? {
              asset_id,
              version,
              definition: frozen.team_definition ?? frozen.definition,
            }
          : listed ?? (await workflowAssetsApi.get(asset_id, version));
        if (!isCurrent()) return;
        pinnedTeams.value[key] = team;
      } catch {
        if (!isCurrent()) return;
        teamReadError.value = true;
        localError.value = tm("child.unavailable");
      }
    }
    if (!isCurrent()) return;
    readingTeams.value = false;
    emit(
      "validation",
      selectingTeam.value ||
        teamReadError.value ||
        !!Object.keys(contractErrors.value).length,
    );
  },
  { immediate: true, deep: true },
);

function toggleLoop(id: string) {
  collapsedLoops.value = collapsedLoops.value.includes(id)
    ? collapsedLoops.value.filter((value) => value !== id)
    : [...collapsedLoops.value, id];
}

watch(
  () => props.definition,
  (value) => {
    teamSelectionRequest++;
    if (selectingTeam.value) {
      selectingTeam.value = false;
      emit(
        "validation",
        readingTeams.value ||
          teamReadError.value ||
          !!Object.keys(contractErrors.value).length,
      );
    }
    if (JSON.stringify(value) === JSON.stringify(draft.value)) return;
    draft.value = JSON.parse(JSON.stringify(value));
    history.value = [JSON.stringify(draft.value)];
    historyIndex.value = 0;
  },
  { deep: true },
);

/** Save an editor checkpoint after a complete user action. */
function checkpoint() {
  if (props.readonly) return;
  const value = JSON.stringify(draft.value);
  if (value === history.value[historyIndex.value]) return;
  history.value = [
    ...history.value.slice(0, historyIndex.value + 1),
    value,
  ].slice(-100);
  historyIndex.value = history.value.length - 1;
  localError.value = "";
  emit("update:definition", JSON.parse(value));
}

/** Restore a full definition, including task properties and layout.
 * Args:
 *   delta: Previous or next checkpoint.
 */
function restore(delta: number) {
  const index = historyIndex.value + delta;
  if (index < 0 || index >= history.value.length) return;
  historyIndex.value = index;
  draft.value = JSON.parse(history.value[index]);
  contractTexts.value = {};
  contractErrors.value = {};
  aliasTexts.value = {};
  localError.value = "";
  emit("validation", false);
  emit("update:definition", JSON.parse(history.value[index]));
}

/** Add a task from a pinned role at a dropped or default canvas position.
 * Args:
 *   roleId: Existing role asset identity.
 *   position: Optional translated flow coordinate.
 */
function addTask(roleId: string, position?: { x: number; y: number }) {
  if (props.readonly) return;
  const role = props.roles.find((item) => item.role_id === roleId);
  const team = roleId.startsWith("team:")
    ? props.teams.find((item) => item.asset_id === roleId.slice(5))
    : null;
  const workflow = roleId.startsWith("workflow:")
    ? props.workflows.find((item) => item.asset_id === roleId.slice(9))
    : null;
  const humanKind =
    roleId === "human:question"
      ? "question"
      : roleId === "human:approval"
      ? "approval"
      : null;
  const controlKind =
    roleId === "control:condition"
      ? "condition"
      : roleId === "control:join"
      ? "join"
      : null;
  if (!role && !humanKind && !team && !workflow && !controlKind) return;
  const seed =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID().replace(/-/g, "").slice(0, 8)
      : `${Date.now()}_${Math.random().toString(16).slice(2)}`;
  let id = `task_${seed}`;
  let suffix = 1;
  // Keep task IDs unique within this canvas even when generated values repeat.
  while (draft.value.tasks.some((task) => task.id === id))
    id = `task_${seed}_${suffix++}`;
  const index = draft.value.tasks.length;
  draft.value.tasks.push({
    id,
    name: controlKind
      ? tm(
          controlKind === "condition"
            ? "graph.conditionNode"
            : "graph.joinNode",
        )
      : team
      ? team.definition.name
      : workflow
      ? workflow.definition.name
      : humanKind
      ? tm(`human.${humanKind}Node`)
      : role!.definition.name,
    ...(controlKind
      ? {
          control:
            controlKind === "condition"
              ? {
                  kind: "condition" as const,
                  operator: "eq" as const,
                  value: "",
                }
              : { kind: "join" as const },
        }
      : team
      ? {
          team: { asset_id: team.asset_id, version: team.version },
          output_contract: team.definition.output_contract,
        }
      : workflow
      ? {
          workflow: { asset_id: workflow.asset_id, version: workflow.version },
          output_contract:
            workflow.definition.tasks.find(
              (item: any) => item.id === workflow.definition.result_task_id,
            )?.output_contract ?? null,
        }
      : humanKind
      ? {
          human: {
            kind: humanKind,
            question: "",
            choices: [],
            max_length: 10000,
            artifact_version: 1,
          },
        }
      : { member: id, role_id: roleId, role_version: role!.version }),
    task: "",
    inputs:
      controlKind === "condition"
        ? {
            value: {
              input: draft.value.required_inputs?.[0] ?? "",
              field: "text",
            },
          }
        : team || workflow
        ? Object.fromEntries(
            (team ?? workflow).definition.required_inputs.map((key: string) => [
              key,
              draft.value.required_inputs?.includes(key) ? { input: key } : {},
            ]),
          )
        : {},
    overrides: {},
    position: position ?? {
      x: (index % 3) * 300,
      y: Math.floor(index / 3) * 220,
    },
  });
  selectedNode.value = id;
  selectedEdgeKey.value = null;
  checkpoint();
}

/** Apply a fixed team version and its declared contracts.
 * Args:
 *   assetId: Accessible autonomous asset identity.
 *   version: Exact chosen version, defaulting to the listed version.
 */
async function changeTeam(assetId: string, version?: number) {
  const task = selectedTask.value;
  if ((!task?.team && !task?.workflow) || props.readonly) return;
  const isTeam = !!task.team;
  const listed = (isTeam ? props.teams : props.workflows).find(
    (item) => item.asset_id === assetId,
  );
  const pinnedVersion = version ?? listed?.version;
  if (!pinnedVersion) return;
  const request = ++teamSelectionRequest;
  const definition = draft.value;
  /** Check ownership after every asynchronous boundary.
   * Returns:
   *   Whether the response still matches the latest choice and editor context.
   */
  const isCurrent = () =>
    request === teamSelectionRequest &&
    draft.value === definition &&
    selectedTask.value === task &&
    !props.readonly;
  selectingTeam.value = true;
  emit("validation", true);
  try {
    const team =
      pinnedTeams.value[`${assetId}:${pinnedVersion}`] ??
      (listed?.version === pinnedVersion
        ? listed
        : await workflowAssetsApi.get(assetId, pinnedVersion));
    if (!isCurrent()) return;
    if ((team.definition.kind === "autonomous") !== isTeam)
      throw new Error("Invalid team asset");
    pinnedTeams.value[`${assetId}:${pinnedVersion}`] = team;
    if (isTeam) task.team = { asset_id: assetId, version: pinnedVersion };
    else task.workflow = { asset_id: assetId, version: pinnedVersion };
    task.inputs = Object.fromEntries(
      team.definition.required_inputs.map((key: string) => [
        key,
        task.inputs?.[key] ??
          (draft.value.required_inputs?.includes(key) ? { input: key } : {}),
      ]),
    );
    task.output_contract = isTeam
      ? team.definition.output_contract
      : team.definition.tasks.find(
          (item: any) => item.id === team.definition.result_task_id,
        )?.output_contract ?? null;
    delete contractTexts.value[task.id];
    teamReadError.value = false;
    localError.value = "";
    checkpoint();
  } catch {
    if (isCurrent()) {
      teamReadError.value = true;
      localError.value = tm("child.unavailable");
    }
  } finally {
    if (isCurrent()) {
      selectingTeam.value = false;
      emit(
        "validation",
        readingTeams.value ||
          teamReadError.value ||
          !!Object.keys(contractErrors.value).length,
      );
    }
  }
}

function dragRole(event: DragEvent, roleId: string) {
  event.dataTransfer?.setData("application/x-member-id", roleId);
}
function selectNode(id: string | null) {
  if (id?.startsWith("loop:")) {
    toggleLoop(id.slice(5));
    return;
  }
  selectedNode.value = id;
  selectedEdgeKey.value = null;
  emit("selectTask", id);
}
function selectEdge(id: string | null) {
  selectedEdgeKey.value = id;
  if (id) selectedNode.value = null;
}
function changeRole(id: string) {
  if (selectedTask.value)
    selectedTask.value.role_version =
      props.roles.find((role) => role.role_id === id)?.version ?? 1;
  checkpoint();
}
function changeSource(source: string) {
  const task = selectedTask.value;
  if (!task) return;
  if (source === "template") {
    delete task.role;
    delete task.installation;
    task.role_id = props.roles[0]?.role_id ?? null;
    task.role_version = props.roles[0]?.version ?? null;
    task.overrides = {};
  } else {
    delete task.role_id;
    delete task.role_version;
    task.overrides = {};
    task.role = { name: task.name };
    task.installation = { profile_id: "default" };
  }
  for (const field of ["tools", "skills", "knowledge_bases"])
    resourceModes.value[field] = "inherit";
  checkpoint();
}
function setOverride(field: string, enabled: boolean | null) {
  const overrides = selectedTask.value?.overrides as
    | Record<string, any>
    | undefined;
  if (!overrides) return;
  if (enabled) overrides[field] = field === "prompt" ? "" : null;
  else delete overrides[field];
  checkpoint();
}
async function saveInlineRole() {
  const task = selectedTask.value;
  if (!task?.role || !task.installation) return;
  savingRole.value = true;
  roleMessage.value = "";
  try {
    await roleAssetsApi.save(null, {
      definition: task.role,
      installation: task.installation,
    });
    if (selectedTask.value?.id === task.id)
      roleMessage.value = tm("graph.roleSaved");
    emit("roleSaved");
  } catch (error: any) {
    if (selectedTask.value?.id === task.id)
      roleMessage.value = extractApiError(
        error,
        tm("errors.operationFailed"),
      ).message;
  } finally {
    savingRole.value = false;
  }
}
function connect({
  from,
  to,
  sourceHandle,
}: {
  from: string;
  to: string;
  sourceHandle?: string | null;
}) {
  if (from.startsWith("__") || to.startsWith("__")) return;
  if (from.startsWith("loop:") || to.startsWith("loop:")) return;
  if (
    !draft.value.edges?.some(
      (edge) => edge.source === from && edge.target === to,
    )
  ) {
    const condition =
      draft.value.tasks.find((task) => task.id === from)?.control?.kind ===
        "condition" ||
      draft.value.tasks.find((task) => task.id === from)?.human?.kind ===
        "approval"
        ? {
            field:
              draft.value.tasks.find((task) => task.id === from)?.human
                ?.kind === "approval"
                ? "approved"
                : "matched",
            operator: "eq" as const,
            value: sourceHandle !== "matched-false",
          }
        : undefined;
    (draft.value.edges ??= []).push({
      source: from,
      target: to,
      ...(condition ? { condition } : {}),
    });
    checkpoint();
  }
}
function setWorkflowInputs(value: string) {
  draft.value.required_inputs = value
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean);
  checkpoint();
}
function move(positions: Record<string, { x: number; y: number }>) {
  if (props.readonly) return;
  for (const task of draft.value.tasks)
    if (positions[task.id]) task.position = positions[task.id];
  checkpoint();
}
function removeEdge(key: string) {
  draft.value.edges = draft.value.edges?.filter(
    (edge) => `${edge.source}:${edge.target}` !== key,
  );
  selectedEdgeKey.value = null;
  checkpoint();
}
function removeTask() {
  const removed = selectedNode.value;
  draft.value.loops = (draft.value.loops ?? [])
    .filter(
      (loop) =>
        loop.exit_task_id !== removed && loop.body.some((id) => id !== removed),
    )
    .map((loop) => ({
      ...loop,
      body: loop.body.filter((id) => id !== removed),
      feedback: (loop.feedback ?? []).filter(
        (item) => item.source !== removed && item.target !== removed,
      ),
    }));
  draft.value.tasks = draft.value.tasks.filter(
    (task) => task.id !== selectedNode.value,
  );
  draft.value.edges = draft.value.edges?.filter(
    (edge) =>
      edge.source !== selectedNode.value && edge.target !== selectedNode.value,
  );
  if (selectedNode.value) {
    delete contractErrors.value[selectedNode.value];
    delete contractTexts.value[selectedNode.value];
    emit("validation", Object.keys(contractErrors.value).length > 0);
  }
  selectedNode.value = null;
  checkpoint();
}
function addInput() {
  if (!selectedTask.value) return;
  const inputs = (selectedTask.value.inputs ??= {});
  let index = 1;
  while (`input${index}` in inputs) index++;
  inputs[`input${index}`] = {
    input: draft.value.required_inputs?.[0] ?? "",
    field: "text",
  };
  checkpoint();
}
function renameInput(alias: string, name: string) {
  const inputs = selectedTask.value?.inputs;
  if (!inputs || alias === name) return;
  if (!/^[a-zA-Z_]\w*$/.test(name) || name in inputs) {
    localError.value = tm("graph.invalidAlias");
    return;
  }
  const taskId = selectedTask.value!.id;
  inputRenderKeys.value[`${taskId}:${name}`] =
    inputRenderKeys.value[`${taskId}:${alias}`] ?? alias;
  inputs[name] = inputs[alias];
  delete inputs[alias];
  checkpoint();
}
function setInputSource(alias: string, source: string) {
  if (!selectedTask.value?.inputs) return;
  selectedTask.value.inputs[alias] = source.startsWith("input:")
    ? { input: source.slice(6), field: "text" }
    : { task_id: source, field: fieldsForTask(source)[0]?.value ?? "text" };
  if (selectedTask.value.control?.kind === "condition") {
    selectedTask.value.control.operator = "eq";
    selectedTask.value.control.value =
      controlValueType.value === "boolean"
        ? false
        : controlValueType.value === "null"
        ? null
        : ["integer", "number"].includes(controlValueType.value)
        ? 0
        : "";
  }
  checkpoint();
}
function toggleCondition(enabled: boolean | null) {
  if (!selectedEdge.value) return;
  if (enabled)
    selectedEdge.value.condition = { field: "text", operator: "eq", value: "" };
  else delete selectedEdge.value.condition;
  checkpoint();
}
function setConditionValue(value: string) {
  if (!selectedEdge.value?.condition) return;
  try {
    selectedEdge.value.condition.value = JSON.parse(value);
  } catch {
    selectedEdge.value.condition.value = value;
  }
  checkpoint();
}

/** Place tasks by dependency depth, preserving definition and role identities. */
function layout() {
  const depths = new Map<string, number>();
  for (let pass = 0; pass < draft.value.tasks.length; pass++) {
    for (const task of draft.value.tasks) {
      const incoming =
        draft.value.edges?.filter((edge) => edge.target === task.id) ?? [];
      if (incoming.every((edge) => depths.has(edge.source)))
        depths.set(
          task.id,
          incoming.length
            ? Math.max(...incoming.map((edge) => depths.get(edge.source)!)) + 1
            : 0,
        );
    }
  }
  const rows = new Map<number, number>();
  for (const task of draft.value.tasks) {
    const depth = depths.get(task.id) ?? 0;
    const row = rows.get(depth) ?? 0;
    task.position = { x: depth * 300, y: row * 220 };
    rows.set(depth, row + 1);
  }
  checkpoint();
  void canvas.value?.fit();
}
</script>

<style scoped>
.child-output-contract {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 180px;
  overflow: auto;
}
.graph-child-preview {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 10px;
  overflow-wrap: anywhere;
}
.graph-team-button {
  width: 100%;
  height: auto;
  min-height: 36px;
  white-space: normal;
}
.graph-team-button :deep(.v-btn__content) {
  white-space: normal;
  overflow-wrap: anywhere;
}
.graph-workbench {
  --graph-height: clamp(460px, calc(100vh - 250px), 640px);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 16px;
  overflow: hidden;
  background: rgb(var(--v-theme-surface));
}
.graph-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 12px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.1);
}
.graph-edge-picker {
  min-width: min(230px, 100%);
  max-width: 320px;
}
.graph-columns {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr) 300px;
  height: var(--graph-height);
  min-height: 0;
}
.graph-columns.is-readonly {
  grid-template-columns: minmax(0, 1fr);
}
.graph-toolbox,
.graph-inspector {
  padding: 16px;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  overflow-wrap: anywhere;
}
.graph-toolbox {
  overflow-x: hidden;
  border-right: 1px solid rgba(var(--v-theme-on-surface), 0.1);
}
.graph-toolbox :deep(.v-btn) {
  width: 100%;
  min-width: 0;
  height: auto;
  min-height: 36px;
  white-space: normal;
}
.graph-toolbox :deep(.v-btn__content) {
  min-width: 0;
  white-space: normal;
  overflow-wrap: anywhere;
}
.graph-inspector {
  border-left: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  overflow-x: hidden;
}
.graph-inspector :deep(.v-input),
.graph-inspector :deep(.v-field) {
  min-width: 0;
  max-width: 100%;
}
summary {
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
}
.graph-role {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 12px 8px;
  margin: 8px 0;
  gap: 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 10px;
  text-align: left;
  color: inherit;
  cursor: grab;
}
.graph-role span {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}
.graph-role strong {
  display: block;
  font-size: 12px;
}
.graph-role small {
  display: block;
  font-size: 10px;
  opacity: 0.7;
  margin-top: 4px;
}
.graph-stage {
  position: relative;
  height: 100%;
  min-width: 0;
  background: rgba(var(--v-theme-primary), 0.025);
}
.graph-stage :deep(.teams-flow-canvas) {
  height: 100%;
  min-height: 0;
}
.graph-empty {
  position: absolute;
  top: 40%;
  left: 10%;
  right: 10%;
  text-align: center;
  pointer-events: none;
  color: rgba(var(--v-theme-on-surface), 0.6);
}
.graph-inspector-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 16px 0;
}
.graph-inspector-heading code {
  width: 100%;
  font-size: 10px;
  opacity: 0.6;
}
.graph-input {
  padding: 10px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 10px;
  margin: 10px 0;
}
.graph-footer {
  padding: 10px 16px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  opacity: 0.7;
}
.graph-stage :deep(.member-flow-node) {
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.16);
  box-shadow: 0 4px 16px rgba(var(--v-theme-on-surface), 0.06);
  padding: 14px;
  width: 240px;
  border-radius: 14px;
}
.graph-stage :deep(.member-flow-node.selected) {
  border-color: rgb(var(--v-theme-primary));
}
.graph-stage :deep(.at-node-title) {
  font-size: 14px;
  font-weight: 600;
}
.graph-stage :deep(.at-node-footer) {
  margin-top: 12px;
  gap: 10px;
}
.graph-stage :deep(.at-node-done) {
  border-color: rgb(var(--v-theme-success));
}
.graph-stage :deep(.at-node-running) {
  border-color: rgb(var(--v-theme-primary));
}
.graph-stage :deep(.at-node-failed) {
  border-color: rgb(var(--v-theme-error));
}
@media (max-width: 1200px) {
  .graph-columns {
    grid-template-columns: 140px minmax(0, 1fr) 260px;
  }
}
@media (max-width: 1000px) {
  .graph-columns {
    grid-template-columns: minmax(0, 1fr);
    height: auto;
  }
  .graph-toolbox {
    border-right: 0;
  }
  .graph-inspector {
    border-left: 0;
    max-height: none;
  }
  .graph-stage {
    height: clamp(260px, 45vh, 460px);
  }
  .graph-role {
    max-width: 260px;
    display: inline-flex;
    margin-right: 8px;
  }
}
</style>

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
    <v-alert
      v-if="localError || Object.keys(contractErrors).length"
      type="error"
      variant="tonal"
      class="ma-3"
      role="alert"
      >{{ localError || Object.values(contractErrors)[0] }}</v-alert
    >
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
        :open="!narrow || !!selectedTask || !!selectedEdge"
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
            v-if="!selectedTask.human && !selectedTask.team"
            v-model="selectedTask.role_id"
            :items="roles"
            item-title="definition.name"
            item-value="role_id"
            :label="tm('v2.role')"
            density="compact"
            @update:model-value="changeRole"
          />
          <v-select
            v-if="!selectedTask.human && !selectedTask.team"
            v-model="selectedTask.role_version"
            :items="selectedRole?.versions ?? [selectedTask.role_version]"
            :label="tm('v2.roleVersion')"
            density="compact"
            @update:model-value="checkpoint"
          />
          <v-text-field
            v-if="!selectedTask.human && !selectedTask.team"
            v-model="selectedTask.member"
            :label="tm('graph.memberKey')"
            :hint="tm('graph.memberHint')"
            persistent-hint
            density="compact"
            @update:model-value="checkpoint"
          />
          <v-textarea
            v-if="!selectedTask.human && !selectedTask.team"
            v-model="selectedTask.task"
            :label="tm('v2.task')"
            :hint="tm('v2.taskHint')"
            persistent-hint
            rows="4"
            density="compact"
            class="mt-4"
            @update:model-value="checkpoint"
          />
          <template v-if="selectedTask.team">
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
          <template v-if="selectedTask.human">
            <v-textarea
              v-model="selectedTask.human.question"
              :label="tm('human.question')"
              data-testid="human-question"
              @update:model-value="checkpoint"
            />
            <template v-if="selectedTask.human.kind === 'approval'">
              <v-select
                v-model="selectedTask.human.artifact_task_id"
                :items="
                  draft.tasks
                    .filter(
                      (task) => task.id !== selectedTask?.id && !task.human,
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
              <v-text-field
                v-model.number="selectedTask.human.max_length"
                type="number"
                min="1"
                :label="tm('human.maxLength')"
                @update:model-value="checkpoint"
              />
            </template>
          </template>
          <template v-else>
            <h3 class="text-subtitle-2 mt-4 mb-2">{{ tm("graph.inputs") }}</h3>
            <div
              v-for="(ref, alias) in selectedTask.inputs"
              :key="inputRenderKeys[`${selectedTask.id}:${alias}`] ?? alias"
              class="graph-input"
            >
              <v-text-field
                :model-value="
                  aliasTexts[`${selectedTask.id}:${alias}`] ?? alias
                "
                :label="tm('graph.inputAlias')"
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
              <v-select
                :model-value="ref.input ? `input:${ref.input}` : ref.task_id"
                :items="inputSources"
                :label="tm('graph.source')"
                density="compact"
                @update:model-value="setInputSource(String(alias), $event)"
              />
              <v-text-field
                v-if="ref.task_id"
                v-model="ref.field"
                :label="tm('graph.field')"
                density="compact"
                @update:model-value="checkpoint"
              />
              <v-btn
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
            <v-btn variant="tonal" size="small" @click="addInput">{{
              tm("graph.addInput")
            }}</v-btn>
            <p class="text-caption my-2">{{ tm("graph.referenceHint") }}</p>
            <h3 class="text-subtitle-2 mt-4 mb-2">{{ tm("graph.outputs") }}</h3>
            <v-textarea
              :key="selectedTask.id"
              :model-value="
                contractTexts[selectedTask.id] ??
                (selectedTask.output_contract
                  ? JSON.stringify(selectedTask.output_contract, null, 2)
                  : '')
              "
              :label="tm('v2.contract')"
              :readonly="!!selectedTask.team"
              :hint="tm('v2.contractHint')"
              persistent-hint
              rows="5"
              density="compact"
              @update:model-value="setContract"
            />
          </template>
          <v-btn
            variant="text"
            color="error"
            class="mt-3"
            @click="removeTask"
            >{{ tm("graph.removeTask") }}</v-btn
          >
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
          <v-switch
            :model-value="!!selectedEdge.condition"
            :label="tm('graph.condition')"
            color="primary"
            density="compact"
            @update:model-value="toggleCondition"
          />
          <template v-if="selectedEdge.condition">
            <v-text-field
              v-model="selectedEdge.condition.field"
              :label="tm('graph.field')"
              density="compact"
              @update:model-value="checkpoint"
            />
            <v-select
              v-model="selectedEdge.condition.operator"
              :items="operators"
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
import type { FlowNode } from "./TeamsFlowCanvas.vue";
import type { GraphDefinition } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";
import { collabMemberColor } from "@/utils/memberColors";
import { workflowAssetsApi } from "@/api/workflowAssets";

const props = withDefaults(
  defineProps<{
    definition: GraphDefinition;
    roles?: any[];
    teams?: any[];
    childAssets?: Record<string, any>;
    readonly?: boolean;
    tasks?: any[];
    dispatchTasks?: string[];
  }>(),
  {
    roles: () => [],
    teams: () => [],
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
}>();
const { tm } = useModuleI18n("features/agent-teams");
const draft = ref<GraphDefinition>(
  JSON.parse(JSON.stringify(props.definition)),
);
const canvas = ref<InstanceType<typeof TeamsFlowCanvas>>();
const selectedNode = ref<string | null>(null);
const selectedEdgeKey = ref<string | null>(null);
const minimap = ref(true);
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
const narrow = window.matchMedia?.("(max-width: 1000px)").matches ?? false;
const history = ref<string[]>([JSON.stringify(draft.value)]);
const historyIndex = ref(0);
const selectedTask = computed(() =>
  draft.value.tasks.find((task) => task.id === selectedNode.value),
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
  teamSelectionRequest++;
  teamReadRequest++;
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
const inputSources = computed(() => [
  ...(draft.value.required_inputs ?? []).map((name) => ({
    title: `${tm("graph.workflowInput")}: ${name}`,
    value: `input:${name}`,
  })),
  ...draft.value.tasks
    .filter((task) => task.id !== selectedNode.value)
    .map((task) => ({ title: task.name, value: task.id })),
]);
const operators = ["eq", "ne", "gt", "ge", "lt", "le"];
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
        memberName: task.team
          ? tm("child.independent")
          : task.human
          ? tm(`human.${task.human.kind}Node`)
          : `${
              role?.definition.name ??
              running?.snapshot.member?.name ??
              task.role_id
            } · ${task.member}`,
        memberColor: collabMemberColor(task.member ?? task.id),
        taskPreview: task.team
          ? pinnedTeams.value[`${task.team.asset_id}:${task.team.version}`]
              ?.definition.name ?? task.team.asset_id
          : task.human?.question ?? task.task,
        nodeNumber: index + 1,
        configLabel: task.team
          ? `v${task.team.version}`
          : task.human
          ? task.human.kind === "approval"
            ? `v${task.human.artifact_version ?? 1}`
            : ""
          : `v${task.role_version}`,
        inputLabel: `${tm("graph.inputs")} · ${
          Object.keys(task.inputs ?? {}).length
        }`,
        outputLabel: task.output_contract
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
        statusLabel: state ? tm(`v2.states.${state}`) : "",
        error: running?.state.reason ?? "",
        inDegree:
          draft.value.edges?.filter((edge) => edge.target === task.id).length ??
          0,
      },
    };
  }),
);
const collapsedGroups = computed(() =>
  (draft.value.loops ?? []).filter((loop) =>
    collapsedLoops.value.includes(loop.id),
  ),
);
const nodes = computed<FlowNode[]>(() => [
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
  (draft.value.edges ?? [])
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
        label: edge.condition
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
    draft.value.tasks.map((task) => task.team),
    props.teams,
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
      if (!task.team) continue;
      const { asset_id, version } = task.team;
      const key = `${asset_id}:${version}`;
      if (pinnedTeams.value[key]) continue;
      const frozen = props.childAssets[task.id];
      const listed = props.teams.find(
        (team) => team.asset_id === asset_id && team.version === version,
      );
      try {
        const team = frozen
          ? { asset_id, version, definition: frozen.team_definition }
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
  const humanKind =
    roleId === "human:question"
      ? "question"
      : roleId === "human:approval"
      ? "approval"
      : null;
  if (!role && !humanKind && !team) return;
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
    name: team
      ? team.definition.name
      : humanKind
      ? tm(`human.${humanKind}Node`)
      : role.definition.name,
    ...(team
      ? {
          team: { asset_id: team.asset_id, version: team.version },
          output_contract: team.definition.output_contract,
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
      : { member: id, role_id: roleId, role_version: role.version }),
    task: "",
    inputs: team
      ? Object.fromEntries(
          team.definition.required_inputs.map((key: string) => [
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
  if (!task?.team || props.readonly) return;
  const listed = props.teams.find((item) => item.asset_id === assetId);
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
    if (team.definition.kind !== "autonomous")
      throw new Error("Invalid team asset");
    pinnedTeams.value[`${assetId}:${pinnedVersion}`] = team;
    task.team = { asset_id: assetId, version: pinnedVersion };
    task.inputs = Object.fromEntries(
      team.definition.required_inputs.map((key: string) => [
        key,
        task.inputs?.[key] ??
          (draft.value.required_inputs?.includes(key) ? { input: key } : {}),
      ]),
    );
    task.output_contract = team.definition.output_contract;
    delete contractTexts.value[task.id];
    teamReadError.value = false;
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
function connect({ from, to }: { from: string; to: string }) {
  if (from.startsWith("loop:") || to.startsWith("loop:")) return;
  if (
    !draft.value.edges?.some(
      (edge) => edge.source === from && edge.target === to,
    )
  ) {
    (draft.value.edges ??= []).push({ source: from, target: to });
    checkpoint();
  }
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
    : { task_id: source, field: "text" };
  checkpoint();
}
function setContract(value: string) {
  if (!selectedTask.value) return;
  const id = selectedTask.value.id;
  contractTexts.value[id] = value;
  try {
    selectedTask.value.output_contract = value.trim()
      ? JSON.parse(value)
      : null;
    delete contractErrors.value[id];
    checkpoint();
  } catch {
    contractErrors.value[id] = tm("graph.invalidJson");
  }
  emit("validation", Object.keys(contractErrors.value).length > 0);
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
  min-width: 230px;
  max-width: 320px;
}
.graph-columns {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr) 300px;
  min-height: 560px;
}
.graph-columns.is-readonly {
  grid-template-columns: minmax(0, 1fr);
}
.graph-toolbox,
.graph-inspector {
  padding: 16px;
  min-width: 0;
  overflow-wrap: anywhere;
}
.graph-toolbox {
  border-right: 1px solid rgba(var(--v-theme-on-surface), 0.1);
}
.graph-inspector {
  border-left: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  max-height: 70vh;
  overflow: auto;
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
  height: 560px;
  min-width: 0;
  background: rgba(var(--v-theme-primary), 0.025);
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
  }
  .graph-toolbox {
    border-right: 0;
  }
  .graph-inspector {
    border-left: 0;
    max-height: none;
  }
  .graph-stage {
    height: 460px;
  }
  .graph-role {
    max-width: 260px;
    display: inline-flex;
    margin-right: 8px;
  }
}
</style>

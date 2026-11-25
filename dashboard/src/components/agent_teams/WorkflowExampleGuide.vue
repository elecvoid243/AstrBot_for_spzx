<template>
  <section
    class="example-guide"
    :class="{ 'motion-paused': motionPaused }"
    data-testid="release-guide"
    :aria-label="tm(prefix + '.guide.title')"
    @keydown.left.prevent="move(-1)"
    @keydown.right.prevent="move(1)"
  >
    <div class="guide-toolbar">
      <strong><v-icon icon="mdi-motion-play-outline" size="18" />{{ tm(prefix + ".guide.title") }}</strong>
      <v-btn variant="text" color="primary" :prepend-icon="playing ? 'mdi-pause' : last ? 'mdi-replay' : 'mdi-play'"
        data-testid="release-guide-play" @click="togglePlayback">
        {{ tm(`releaseBrief.guide.${playing ? "pause" : last ? "replay" : "play"}`) }}
      </v-btn>
    </div>

    <div class="guide-illustration" data-testid="release-guide-graph" aria-hidden="true">
      <div v-if="current.id === 'canvas'" class="guide-ports">
        <div>{{ tm("graph.workflowInput") }}<span class="guide-data-source">project · string ●</span></div>
        <i class="guide-port-wire"><i class="guide-packet" /></i>
        <div>{{ tm("releaseBrief.guide.nodes.prepare") }}<span class="guide-control-target">○ {{ tm("graph.controlIn") }}</span><span class="guide-data-target">● project · string</span></div>
      </div>
      <div v-else class="guide-track">
        <div v-for="(node, index) in nodes" :key="node.id" class="guide-node"
          :class="{ 'is-current': current.node === index, 'is-review': node.id === 'review' }">
          <span class="guide-node-icon"><v-icon :icon="node.icon" size="21" /></span>
          <span class="guide-node-label">{{ tm(`${prefix}.guide.nodes.${node.id}`) }}</span>
          <span v-if="index < nodes.length - 1" class="guide-wire"
            :class="{ 'is-active': current.edge === index }">
            <v-icon v-if="kind === 'workflow' && index === 2 && (current.id === 'review' || last)" class="guide-gate"
              :icon="last ? 'mdi-lock-open-check-outline' : 'mdi-lock-outline'" size="14" />
            <i v-if="current.edge === index" :key="current.id" class="guide-packet" />
          </span>
        </div>
        <div v-if="kind === 'workflow'" class="guide-binding" :class="{ 'is-active': last }">
          <i v-if="last" class="guide-data-packet" />
          <code>project · summary</code>
        </div>
      </div>
    </div>

    <div class="guide-caption" data-testid="release-guide-step" :data-step="current.id" aria-live="polite" aria-atomic="true">
      <span class="guide-counter">{{ tm("releaseBrief.guide.progress", { step: step + 1, total: steps.length }) }}</span>
      <h3>{{ tm(`${prefix}.guide.steps.${current.id}.title`) }}</h3>
      <p>{{ tm(`${prefix}.guide.steps.${current.id}.body`, {
        canvas: tm('releaseBrief.canvas'), start: tm('v2.start'), chat: tm('runConversation.openChat'),
        inbox: tm('workbench.panelresults'), preview: tm('human.preview'), approve: tm('human.approve'), edit: tm('roles.edit'), publish: tm('draft.publish'), rolePreview: tm('roles.preview'),
      }) }}</p>
      <q v-if="kind === 'team' && current.id === 'goal'" class="guide-question">{{ tm("starterTeam.goal") }}</q>
      <q v-if="current.id === 'review'" class="guide-question" lang="en">Approve this release brief?</q>
      <dl v-if="kind === 'workflow' && (current.id === 'start' || last)" class="guide-values">
        <div><dt>project</dt><dd>Agent Teams</dd></div>
        <div><dt>summary</dt><dd>Ready for release.</dd></div>
      </dl>
    </div>

    <div class="guide-navigation">
      <div class="guide-progress" aria-hidden="true">
        <i v-for="(_, index) in steps" :key="index" :class="{ 'is-complete': index <= step }" />
      </div>
      <div class="guide-buttons">
        <v-btn variant="text" prepend-icon="mdi-chevron-left" :disabled="step === 0"
          data-testid="release-guide-previous" @click="move(-1)">{{ tm("releaseBrief.guide.previous") }}</v-btn>
        <v-btn variant="tonal" color="primary" append-icon="mdi-chevron-right" :disabled="last"
          data-testid="release-guide-next" @click="move(1)">{{ tm("releaseBrief.guide.next") }}</v-btn>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";

const { tm } = useModuleI18n("features/agent-teams");
const props = withDefaults(defineProps<{ kind?: "workflow" | "team" | "role" }>(), { kind: "workflow" });
const prefix = computed(() => ({ workflow: "releaseBrief", team: "starterTeam", role: "starterRole" })[props.kind]);
const nodes = computed(() => props.kind === "team" ? [
  { id: "lead", icon: "mdi-account-tie" }, { id: "editor", icon: "mdi-file-document-edit-outline" },
  { id: "check", icon: "mdi-account-check-outline" }, { id: "chat", icon: "mdi-message-outline" },
] : props.kind === "role" ? [
  { id: "prompt", icon: "mdi-account-outline" }, { id: "model", icon: "mdi-cog-outline" },
  { id: "version", icon: "mdi-source-branch" }, { id: "agent", icon: "mdi-account-multiple-outline" },
] : [
  { id: "inputs", icon: "mdi-form-textbox" },
  { id: "prepare", icon: "mdi-file-document-edit-outline" },
  { id: "review", icon: "mdi-account-check-outline" },
  { id: "deliver", icon: "mdi-file-check-outline" },
]);
const steps = computed(() => props.kind === "team" ? [
  { id: "configure", node: 0, edge: null }, { id: "goal", node: 0, edge: null },
  { id: "delegate", node: 1, edge: 0 }, { id: "collect", node: 2, edge: 1 },
  { id: "monitor", node: 3, edge: 2 },
] : props.kind === "role" ? [
  { id: "responsibility", node: 0, edge: null }, { id: "resources", node: 1, edge: 0 },
  { id: "publish", node: 2, edge: 1 }, { id: "reuse", node: 3, edge: 2 },
] : [
  { id: "canvas", node: null, edge: null },
  { id: "start", node: 1, edge: 0 },
  { id: "chat", node: 2, edge: 1 },
  { id: "review", node: 2, edge: null },
  { id: "result", node: 3, edge: 2 },
]);
const step = ref(0);
const current = computed(() => steps.value[step.value]);
const last = computed(() => step.value === steps.value.length - 1);
const playing = ref(false);
const motionPaused = ref(false);
let timer: number | undefined;

function pause(freezeIllustration = true) {
  window.clearInterval(timer);
  playing.value = false;
  motionPaused.value = freezeIllustration;
}

function move(direction: number) {
  pause(false);
  step.value = Math.min(steps.value.length - 1, Math.max(0, step.value + direction));
}

function togglePlayback() {
  if (playing.value) {
    pause();
    return;
  }
  if (last.value) step.value = 0;
  playing.value = true;
  motionPaused.value = false;
  timer = window.setInterval(() => {
    step.value++;
    // The walkthrough stops at the same human decision as the real workflow.
    if (current.value.id === "review" || last.value) pause(false);
  }, 5500);
}

function onVisibilityChange() {
  if (document.hidden) pause();
}

watch(() => props.kind, () => {
  pause(false);
  step.value = 0;
});
onMounted(() => document.addEventListener("visibilitychange", onVisibilityChange));
onBeforeUnmount(() => {
  pause();
  document.removeEventListener("visibilitychange", onVisibilityChange);
});
</script>

<style scoped>
.example-guide {
  container-type: inline-size;
  min-width: 0;
  color: var(--at-ink);
  --gate-color: color-mix(in srgb, #b57820 70%, var(--at-ink));
}
.guide-toolbar, .guide-toolbar strong, .guide-navigation, .guide-buttons {
  display: flex;
  align-items: center;
  gap: 8px;
}
.guide-toolbar, .guide-navigation { justify-content: space-between; flex-wrap: wrap; }
.guide-toolbar { margin-bottom: 8px; }
.guide-toolbar strong { font-size: 13px; }
.guide-toolbar strong .v-icon { color: var(--at-accent); }
.guide-illustration {
  padding: 20px 8px 19px;
  border: 1px solid var(--at-line);
  border-radius: 10px;
  background-color: var(--at-canvas);
  background-image: radial-gradient(var(--at-line) 1px, transparent 1px);
  background-size: 12px 12px;
}
.guide-ports { display: grid; grid-template-columns: auto minmax(24px,100px) auto; justify-content: center; align-items: end; padding: 4px; font-size: 12px; }
.guide-ports > div { display: grid; gap: 4px; border: 1px solid var(--at-line); background: var(--at-surface); padding: 8px; border-radius: 6px; }
.guide-ports span { font-size: 11px; }
.guide-control-target { color: var(--at-accent); }
.guide-data-target, .guide-data-source { color: var(--at-ink); }
.guide-data-target { outline: 2px solid var(--at-accent); outline-offset: 1px; border-radius: 2px; }
.guide-port-wire { position: relative; margin-bottom: 16px; color: var(--at-accent); border-top: 1px dashed currentColor; }
.guide-track {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  max-width: 680px;
  margin: 0 auto;
  --radius: 21px;
}
.guide-node { position: relative; display: grid; align-content: start; justify-items: center; gap: 7px; }
.guide-node-icon {
  display: grid;
  place-items: center;
  width: calc(2 * var(--radius));
  height: calc(2 * var(--radius));
  border: 1px solid var(--at-line);
  border-radius: 11px;
  color: var(--at-ink);
  background: var(--at-surface);
}
.guide-node.is-review .guide-node-icon { color: var(--gate-color); }
.guide-node.is-current .guide-node-icon {
  border-color: currentColor;
  color: var(--at-accent);
  background: var(--at-accent-soft);
  box-shadow: 0 0 0 3px color-mix(in srgb, currentColor 12%, transparent);
}
.guide-node.is-current.is-review .guide-node-icon {
  color: var(--gate-color);
  background: color-mix(in srgb, var(--gate-color) 10%, var(--at-surface));
}
.guide-node.is-current .guide-node-icon { animation: guide-arrival .45s ease-out; }
.guide-node-label { padding: 0 2px; font-size: 12px; font-weight: 600; line-height: 1.35; text-align: center; overflow-wrap: anywhere; }
.guide-wire {
  position: absolute;
  left: calc(50% + var(--radius) + 3px);
  top: var(--radius);
  width: calc(100% - 2 * var(--radius) - 6px);
  border-top: 1px solid currentColor;
  color: color-mix(in srgb, var(--at-muted) 65%, var(--at-line));
}
.guide-wire::after {
  content: "";
  position: absolute;
  right: 1px;
  top: -3px;
  width: 5px;
  height: 5px;
  border-top: 1px solid currentColor;
  border-right: 1px solid currentColor;
  transform: rotate(45deg);
}
.guide-gate {
  position: absolute;
  left: 50%;
  bottom: 4px;
  transform: translateX(-50%);
  color: var(--gate-color);
}
.guide-wire.is-active, .guide-binding.is-active { color: var(--at-accent); }
.guide-packet, .guide-data-packet {
  position: absolute;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0;
}
.guide-packet { top: -3.5px; animation: guide-flow 1.4s ease-in-out 2; }
.guide-binding {
  position: relative;
  grid-column: 2 / 5;
  width: 66.6667%;
  justify-self: center;
  height: 21px;
  margin-top: 7px;
  border: 1px dashed currentColor;
  border-top: 0;
  border-radius: 0 0 6px 6px;
  color: var(--at-muted);
}
.guide-binding::after {
  content: "";
  position: absolute;
  top: 0;
  right: -3px;
  width: 5px;
  height: 5px;
  border-top: 1px solid currentColor;
  border-left: 1px solid currentColor;
  transform: rotate(45deg);
}
.guide-binding code {
  position: absolute;
  left: 50%;
  bottom: 0;
  transform: translate(-50%, 50%);
  padding: 1px 7px;
  white-space: nowrap;
  font-size: 11px;
  color: var(--at-ink);
  background: var(--at-canvas);
}
.guide-data-packet { transform: translate(-50%, -50%); animation: guide-binding-flow 2.1s .15s ease-in-out 2; }
.guide-caption { padding: 16px 0; }
.guide-counter { font-size: 11px; font-weight: 650; color: var(--at-accent); }
.guide-caption h3 { margin: 4px 0; font-size: 16px; line-height: 1.4; }
.guide-caption p { margin: 0; max-width: 78ch; font-size: 13px; line-height: 1.65; }
.guide-question { display: block; margin-top: 8px; font-size: 13px; font-weight: 600; }
.guide-values { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 8px; }
.guide-values div { display: flex; align-items: baseline; gap: 6px; }
.guide-values dt { font: 11px monospace; color: var(--at-muted); }
.guide-values dd { margin: 0; font-size: 12px; font-weight: 550; }
.guide-progress { display: flex; gap: 4px; }
.guide-progress i { width: 16px; height: 3px; border-radius: 3px; background: var(--at-line); }
.guide-progress .is-complete { background: var(--at-accent); }
@keyframes guide-arrival { from { transform: scale(.9); } to { transform: scale(1); } }
@keyframes guide-flow {
  0% { left: 0; opacity: 0; }
  15%, 85% { opacity: 1; }
  100% { left: calc(100% - 5px); opacity: 0; }
}
@keyframes guide-binding-flow {
  0% { top: 0; left: 0; opacity: 0; }
  15% { top: 100%; left: 0; opacity: 1; }
  85% { top: 100%; left: 100%; opacity: 1; }
  100% { top: 0; left: 100%; opacity: 0; }
}
.motion-paused .guide-node.is-current .guide-node-icon,
.motion-paused .guide-packet,
.motion-paused .guide-data-packet { animation-play-state: paused; }
@container (max-width: 400px) {
  .guide-track { --radius: 16px; }
  .guide-node-icon { border-radius: 8px; }
  .guide-node-label { font-size: 11px; }
  .guide-progress i { width: 9px; }
}
@media (prefers-reduced-motion: reduce) {
  .guide-node.is-current .guide-node-icon, .guide-packet, .guide-data-packet { animation: none; }
}
</style>

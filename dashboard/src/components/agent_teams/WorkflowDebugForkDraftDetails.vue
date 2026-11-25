<template>
  <section class="mb-3" data-testid="debug-fork-draft-details">
    <p class="font-weight-medium">
      {{ definition.workflow_name }} · {{ tm("debugFork.currentRevision", { revision: definition.draft_revision }) }} · {{ definition.node_name }}
    </p>
    <ul v-if="definition.changes.length" class="text-body-2 pl-4 mt-1">
      <li v-for="change in definition.changes" :key="change.kind + change.name">
        {{ tm(`debugFork.changes.${change.kind}`, { name: change.name }) }}
        <span v-if="change.before || change.after" class="text-medium-emphasis">
          · {{ change.before && change.after && change.before !== change.after
            ? `${change.before} → ${change.after}`
            : change.after || change.before }}
        </span>
        <span v-if="change.required != null" class="text-medium-emphasis">
          · {{ tm(change.required ? "debugFork.required" : "debugFork.optional") }}
        </span>
      </li>
    </ul>
    <p v-else class="text-body-2 text-medium-emphasis">{{ tm("debugFork.noNodeChanges") }}</p>
  </section>
</template>

<script setup lang="ts">
import type { WorkflowDebugForkDraft } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";

defineProps<{ definition: WorkflowDebugForkDraft }>();
const { tm } = useModuleI18n("features/agent-teams");
</script>

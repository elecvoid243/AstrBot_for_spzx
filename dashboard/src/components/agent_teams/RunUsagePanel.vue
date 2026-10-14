<template>
  <section v-if="run.usage" class="usage-panel mb-4" data-testid="run-usage">
    <h3 class="text-subtitle-1 mb-2">{{ tm("usage.title") }}</h3>
    <div class="usage-values">
      <p data-testid="reported-tokens">
        {{ tm("usage.reported") }}:
        {{ run.usage.tokens?.reported ?? tm("usage.unknown") }}
      </p>
      <p>
        {{ tm("usage.estimated") }}:
        {{ run.usage.tokens?.estimated ?? tm("usage.unknown") }}
      </p>
      <p>
        {{ tm("usage.reserved") }}:
        {{ run.usage.tokens?.reserved ?? tm("usage.unknown") }}
      </p>
      <p>
        {{ tm("usage.unknownCalls") }}:
        {{ run.usage.tokens?.unknown_calls ?? tm("usage.unknown") }}
      </p>
    </div>
    <p class="text-body-2 mt-2">
      {{ tm("usage.calls") }}: {{ run.usage.steps_used ?? tm("usage.unknown") }}
    </p>
    <p v-for="(value, key) in run.usage.limits" :key="key" class="text-caption">
      {{ tm(`usage.${key}`) }}: {{ value ?? tm("usage.unlimited") }}
    </p>
    <div
      v-for="constraint in run.usage.constraints ?? []"
      :key="constraint.run_id"
      class="text-caption usage-identity"
    >
      {{ tm("usage.scope") }} {{ constraint.run_id }} ·
      {{ tm("graph.parallel") }}:
      {{ constraint.max_parallel ?? tm("usage.unlimited") }} ·
      {{ tm("child.maxDepth") }}:
      {{ constraint.max_depth ?? tm("usage.unlimited") }}
      <p v-for="(value, key) in constraint.limits ?? {}" :key="key">
        {{ tm(`usage.${key}`) }}: {{ value }}
      </p>
    </div>
    <p
      v-if="run.state?.limit_stop && !run.usage.stops?.length"
      class="text-body-2 mt-2"
      role="status"
    >
      {{ tm("usage.stopped") }}:
      {{ tm(`usage.${run.state.limit_stop.limit}`) }} ({{
        run.state.limit_stop.value
      }})
    </p>
    <p
      v-for="(stop, index) in run.usage.stops ?? []"
      :key="index"
      class="text-body-2 mt-2 usage-identity"
      role="status"
    >
      {{ tm("usage.stopped") }}: {{ tm(`usage.${stop.limit}`) }} ({{
        stop.value
      }}) · {{ tm("usage.task") }} {{ stop.task_run_id }}
    </p>
    <p class="text-caption text-medium-emphasis mt-2">
      {{ tm("usage.boundary") }}
    </p>
    <details v-if="run.usage.ledger?.length" class="mt-3">
      <summary>
        {{ tm("usage.details") }} ({{ run.usage.ledger.length }})
      </summary>
      <article
        v-for="receipt in run.usage.ledger"
        :key="receipt.receipt_id"
        class="usage-receipt mt-3"
      >
        <strong
          >{{ receipt.name ?? receipt.kind }} ·
          {{ tm(`usage.quality.${receipt.quality}`) }}</strong
        >
        <p>
          {{ tm("usage.tokens") }}:
          {{
            receipt.quality === "reported"
              ? receipt.reported_tokens
              : receipt.quality === "estimated"
              ? receipt.estimated_tokens
              : tm("usage.unknown")
          }}
        </p>
        <p
          v-if="
            receipt.quality === 'unknown' && receipt.estimated_tokens != null
          "
        >
          {{ tm("usage.estimated") }}: {{ receipt.estimated_tokens }}
        </p>
        <p class="usage-identity">
          {{ tm("usage.scope") }}: {{ receipt.scope_id }} ·
          {{ tm("usage.task") }}: {{ receipt.run_id }}
        </p>
        <p class="usage-identity">
          {{ tm("v2.attempt") }}: {{ receipt.attempt_id }}
        </p>
        <p class="usage-identity">
          {{ tm("usage.invocation") }}: {{ receipt.invocation_id }} ·
          {{ tm("usage.delivery") }}: {{ receipt.receipt_id }}
        </p>
      </article>
    </details>
  </section>
</template>

<script setup lang="ts">
import { useModuleI18n } from "@/i18n/composables";
defineProps<{ run: any }>();
const { tm } = useModuleI18n("features/agent-teams");
</script>

<style scoped>
.usage-panel {
  padding: 16px;
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 8px;
  min-width: 0;
}
.usage-values {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
}
.usage-identity {
  overflow-wrap: anywhere;
  font-size: 12px;
}
.usage-receipt {
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  padding-top: 8px;
}
summary {
  cursor: pointer;
}
</style>

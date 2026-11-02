<template>
  <section data-testid="human-wait-policy">
    <v-switch
      v-if="override"
      :model-value="modelValue !== undefined"
      :label="tm('humanWait.override')"
      @update:model-value="emit('update:modelValue', $event ? null : undefined)"
    />
    <template v-if="!override || modelValue !== undefined">
      <v-switch
        :model-value="modelValue?.timeout_seconds != null"
        :label="tm('humanWait.enableDeadline')"
        @update:model-value="
          emit(
            'update:modelValue',
            $event ? { timeout_seconds: 60, timeout_action: 'wait' } : null,
          )
        "
      />
      <p v-if="modelValue?.timeout_seconds == null">
        {{ tm("humanWait.unlimited") }}
      </p>
      <template v-else>
        <v-text-field
          type="number"
          min="1"
          :max="unit === 60 ? 525600 : 31536000"
          :model-value="duration"
          :label="tm('humanWait.duration')"
          :error-messages="valid ? [] : [tm('humanWait.invalid')]"
          @update:model-value="setDuration"
        />
        <v-select
          v-model="unit"
          :items="[
            { title: tm('humanWait.seconds'), value: 1 },
            { title: tm('humanWait.minutes'), value: 60 },
          ]"
          :label="tm('humanWait.unit')"
        />
        <v-select
          :model-value="modelValue.timeout_action"
          :items="[
            { title: tm('humanWait.wait'), value: 'wait' },
            {
              title: tm('humanWait.recommendation'),
              value: 'use_recommendation',
            },
          ]"
          :label="tm('humanWait.action')"
          @update:model-value="
            emit('update:modelValue', { ...modelValue, timeout_action: $event })
          "
        />
        <p class="text-caption">{{ tm("humanWait.hint") }}</p>
      </template>
    </template>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import type { HumanWaitPolicy } from "@/api/generated/openapi-v1";
const props = defineProps<{
  modelValue?: HumanWaitPolicy | null;
  override?: boolean;
}>();
const emit = defineEmits<{
  (event: "update:modelValue", value: HumanWaitPolicy | null | undefined): void;
}>();
const { tm } = useModuleI18n("features/agent-teams");
const unit = ref(1);
const duration = ref<number | string>(props.modelValue?.timeout_seconds ?? 60);
const valid = computed(
  () =>
    Number.isInteger(Number(duration.value)) &&
    Number(duration.value) >= 1 &&
    Number(duration.value) * unit.value <= 31536000,
);
watch(
  () => props.modelValue?.timeout_seconds,
  (value) => {
    if (value != null && Number(duration.value) * unit.value !== value)
      duration.value = value / unit.value;
  },
);
watch(unit, () => {
  duration.value = (props.modelValue?.timeout_seconds ?? 60) / unit.value;
});
function setDuration(value: string) {
  duration.value = value;
  if (props.modelValue)
    emit("update:modelValue", {
      ...props.modelValue,
      timeout_seconds: valid.value ? Number(value) * unit.value : 0,
    });
}
</script>

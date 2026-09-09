<template>
  <v-dialog v-model="showDialog" max-width="500px">
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        <v-icon class="mr-2" color="warning">mdi-alert</v-icon>
        {{ tm("messages.importMissingTitle") }}
      </v-card-title>
      <v-card-text>
        <p class="text-body-2 text-medium-emphasis mb-3">
          {{ tm("messages.importMissingHint") }}
        </p>

        <div class="missing-items-list">
          <template v-if="missingTools.length > 0">
            <h4 class="text-subtitle-1 mb-2">
              {{ tm("messages.importMissingTools") }}
            </h4>
            <div class="d-flex flex-wrap ga-1 mb-3">
              <v-chip
                v-for="name in missingTools"
                :key="`tool-${name}`"
                size="small"
                color="warning"
                variant="tonal"
              >
                {{ name }}
              </v-chip>
            </div>
          </template>

          <template v-if="missingSkills.length > 0">
            <h4 class="text-subtitle-1 mb-2">
              {{ tm("messages.importMissingSkills") }}
            </h4>
            <div class="d-flex flex-wrap ga-1">
              <v-chip
                v-for="name in missingSkills"
                :key="`skill-${name}`"
                size="small"
                color="warning"
                variant="tonal"
              >
                {{ name }}
              </v-chip>
            </div>
          </template>
        </div>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="showDialog = false">
          {{ tm("buttons.cancel") }}
        </v-btn>
        <v-btn color="warning" variant="tonal" @click="confirmImport">
          {{ tm("messages.importMissingConfirm") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script lang="ts">
import { defineComponent, type PropType } from "vue";
import { useModuleI18n } from "@/i18n/composables";

export default defineComponent({
  name: "ImportMissingDialog",
  props: {
    modelValue: {
      type: Boolean,
      default: false,
    },
    missingTools: {
      type: Array as PropType<string[]>,
      default: () => [],
    },
    missingSkills: {
      type: Array as PropType<string[]>,
      default: () => [],
    },
  },
  emits: ["update:modelValue", "confirm"],
  setup() {
    const { tm } = useModuleI18n("features/persona");
    return { tm };
  },
  computed: {
    showDialog: {
      get(): boolean {
        return this.modelValue;
      },
      set(value: boolean) {
        this.$emit("update:modelValue", value);
      },
    },
  },
  methods: {
    confirmImport() {
      // Emit before closing so the parent resolves the pending import first
      this.$emit("confirm");
      this.showDialog = false;
    },
  },
});
</script>

<style scoped>
.missing-items-list {
  max-height: 300px;
  overflow-y: auto;
}
</style>

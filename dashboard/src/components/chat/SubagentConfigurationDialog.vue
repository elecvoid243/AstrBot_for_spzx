<template>
  <v-dialog
    :model-value="true"
    max-width="780"
    scrollable
    @update:model-value="!$event && emit('close')"
  >
    <v-card class="subagent-config-dialog" rounded="xl">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ tm("subagentConfig.title") }}
      </v-card-title>
      <div class="config-identity">
        <v-icon size="18">mdi-robot-outline</v-icon>
        <strong>{{ run.agent_name }}</strong>
        <span
          >{{ tm("subagentPages.invocation") }}
          {{ run.subagent_run_id.slice(-6) }}</span
        >
      </div>
      <v-card-text class="config-content">
        <div v-if="loading" class="config-loading" role="status">
          <v-progress-circular indeterminate size="22" width="2" />
          {{ tm("subagentPages.loading") }}
        </div>
        <v-alert v-else-if="failed" type="error" variant="tonal">
          {{ tm("subagentConfig.loadFailed") }}
        </v-alert>
        <v-alert
          v-else-if="!configuration || !Object.keys(configuration).length"
          type="info"
          variant="tonal"
        >
          {{ tm("subagentConfig.legacy") }}
        </v-alert>
        <template v-else>
          <p v-if="configuration.description" class="config-description">
            {{ configuration.description }}
          </p>
          <dl class="config-properties">
            <div>
              <dt>Provider ID</dt>
              <dd>{{ configuration.provider_id || "—" }}</dd>
            </div>
            <div>
              <dt>{{ tm("subagentConfig.contextMode") }}</dt>
              <dd>{{ configuration.context_inherit_mode || "—" }}</dd>
            </div>
            <div>
              <dt>{{ tm("subagentConfig.timeout") }}</dt>
              <dd>
                {{
                  configuration.execution_timeout != null
                    ? `${configuration.execution_timeout}s`
                    : "—"
                }}
              </dd>
            </div>
            <div v-if="configuration.workdir">
              <dt>{{ tm("subagentConfig.workdir") }}</dt>
              <dd>{{ configuration.workdir }}</dd>
            </div>
          </dl>
          <section class="config-section">
            <h3>
              {{ tm("subagentConfig.tools") }}
              <small>{{ configuration.tools?.length || 0 }}</small>
            </h3>
            <div v-if="configuration.tools?.length" class="config-tags">
              <span v-for="tool in configuration.tools" :key="tool">{{
                tool
              }}</span>
            </div>
            <p v-else class="config-empty">{{ tm("subagentConfig.none") }}</p>
            <details
              v-if="configuration.blocked_tools?.length"
              class="blocked-tools"
            >
              <summary>
                {{ tm("subagentConfig.blockedTools") }} ·
                {{ configuration.blocked_tools.length }}
              </summary>
              <div class="config-tags">
                <span v-for="tool in configuration.blocked_tools" :key="tool">{{
                  tool
                }}</span>
              </div>
            </details>
          </section>
          <section class="config-section">
            <h3>
              Skills <small>{{ configuration.skills?.length || 0 }}</small>
            </h3>
            <div v-if="configuration.skills?.length" class="config-tags">
              <span v-for="skill in configuration.skills" :key="skill">{{
                skill
              }}</span>
            </div>
            <p v-else class="config-empty">{{ tm("subagentConfig.none") }}</p>
          </section>
          <section class="config-section">
            <h3>{{ tm("subagentConfig.systemPrompt") }}</h3>
            <pre class="config-prompt">{{
              configuration.system_prompt || tm("subagentConfig.none")
            }}</pre>
          </section>
        </template>
      </v-card-text>
      <v-card-actions class="px-6 pb-4">
        <span class="config-snapshot-note">{{
          tm("subagentConfig.snapshot")
        }}</span>
        <v-spacer />
        <v-btn variant="tonal" @click="emit('close')">{{
          tm("subagentConfig.close")
        }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import {
  remoteConnectionsApi,
  type RemoteResourceScope,
} from "@/api/remoteConnections";
import { chatApi } from "@/api/v1";
import { accountSessionRevision } from "@/utils/accountSession";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{
  sessionId: string;
  remoteScope?: RemoteResourceScope;
  run: { subagent_run_id: string; agent_name: string };
}>();
const emit = defineEmits<{ close: [] }>();
const { tm } = useModuleI18n("features/chat");
const configuration = ref<{
  description?: string;
  provider_id?: string;
  context_inherit_mode?: string;
  execution_timeout?: number;
  workdir?: string;
  tools?: string[];
  blocked_tools?: string[];
  skills?: string[];
  system_prompt?: string;
} | null>(null);
const loading = ref(true);
const failed = ref(false);

watch(
  [
    () => props.sessionId,
    () => props.run.subagent_run_id,
    accountSessionRevision,
    () => JSON.stringify(props.remoteScope),
  ],
  async ([sessionId, runId], _, onCleanup) => {
    const controller = new AbortController();
    onCleanup(() => controller.abort());
    configuration.value = null;
    loading.value = true;
    failed.value = false;
    try {
      const response = props.remoteScope
        ? await remoteConnectionsApi.subagentConfiguration(
            props.remoteScope,
            String(runId),
            controller.signal,
          )
        : await chatApi.getSubagentConfiguration(
            String(sessionId),
            String(runId),
            { signal: controller.signal },
          );
      if (controller.signal.aborted) return;
      if (response.data?.status !== "ok")
        throw new Error("Configuration unavailable");
      configuration.value = response.data.data;
    } catch {
      if (!controller.signal.aborted) failed.value = true;
    } finally {
      if (!controller.signal.aborted) loading.value = false;
    }
  },
  { immediate: true },
);
</script>

<style scoped>
.config-identity {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 24px 16px;
}
.config-identity span,
.config-snapshot-note {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
.config-content {
  padding: 0 24px 20px !important;
}
.config-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px;
}
.config-description {
  margin-bottom: 16px;
}
.config-properties {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  padding: 16px;
  border-radius: 12px;
  background: rgba(var(--v-theme-on-surface), 0.035);
}
.config-properties dt,
.config-empty {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
.config-properties dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
  font-size: 13px;
}
.config-section {
  margin-top: 22px;
}
.config-section h3 {
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 600;
}
.config-section h3 small {
  margin-left: 5px;
  color: rgba(var(--v-theme-on-surface), 0.45);
}
.config-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.config-tags span {
  padding: 4px 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 6px;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.config-prompt {
  padding: 14px;
  border-radius: 10px;
  background: rgba(var(--v-theme-on-surface), 0.035);
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: inherit;
}
.blocked-tools {
  margin-top: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 12px;
}
.blocked-tools summary {
  cursor: pointer;
  margin-bottom: 8px;
}
@media (max-width: 600px) {
  .config-properties {
    grid-template-columns: minmax(0, 1fr);
  }
  .config-snapshot-note {
    max-width: 70%;
  }
}
</style>

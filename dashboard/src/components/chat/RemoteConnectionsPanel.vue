<template>
  <section class="remote-connections">
    <div class="d-flex flex-wrap align-center justify-space-between">
      <strong>{{ tm("remoteConnections.title") }}</strong>
      <v-btn variant="text" size="small" @click="manage = true">{{
        tm("remoteConnections.manage")
      }}</v-btn>
    </div>
    <p v-if="!enabled" class="text-caption">
      {{ tm("remoteConnections.disabled") }}
    </p>
    <details
      v-for="connection in connections"
      :key="connection.connection_id"
      open
    >
      <summary>
        {{ connection.name }}
        <small>{{
          tm(
            `remoteConnections.status.${
              enabled
                ? status[connection.connection_id] || "loading"
                : "disabled"
            }`,
          )
        }}</small>
      </summary>
      <template v-if="enabled && status[connection.connection_id] === 'ready'">
        <details
          v-for="[projectId, rows] in groups(connection.connection_id)"
          :key="projectId"
          open
          class="remote-project"
        >
          <summary>
            {{
              projectId
                ? `${rows[0].project_emoji || "📁"} ${rows[0].project_name}`
                : tm("conversation.title")
            }}
          </summary>
          <button
            v-for="session in rows"
            :key="session.session_id"
            type="button"
            class="remote-session"
            :class="{
              selected:
                selectedConnection === connection.connection_id &&
                selectedSession === session.session_id,
            }"
            @click="
              $emit('select', connection.connection_id, session.session_id)
            "
          >
            {{ session.display_name || session.session_id }}
          </button>
        </details>
      </template>
    </details>
    <v-dialog v-model="manage" max-width="640" scrollable>
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("remoteConnections.manage")
        }}</v-card-title>
        <v-card-text>
          <v-alert v-if="!enabled" type="info" variant="tonal">{{
            tm("remoteConnections.disabled")
          }}</v-alert>
          <div
            v-for="connection in connections"
            :key="connection.connection_id"
            class="d-flex align-center my-2"
          >
            <div class="flex-grow-1">
              {{ connection.name }}
              <div class="text-caption">{{ connection.base_url }}</div>
            </div>
            <v-btn
              variant="text"
              :disabled="!enabled || saving"
              @click="edit(connection)"
              >{{ t("core.common.edit") }}</v-btn
            >
            <v-btn
              variant="text"
              :disabled="!enabled || saving"
              @click="remove(connection.connection_id)"
              >{{ t("core.common.delete") }}</v-btn
            >
          </div>
          <v-divider class="my-4" />
          <v-form @submit.prevent="save">
            <v-text-field
              v-model="form.name"
              :label="tm('remoteConnections.name')"
              maxlength="128"
              :disabled="!enabled"
            />
            <v-text-field
              v-model="form.base_url"
              :label="tm('remoteConnections.address')"
              placeholder="http://192.168.1.20:6185"
              :disabled="!enabled"
            />
            <v-text-field
              v-model="form.key"
              :label="
                tm(
                  editing
                    ? 'remoteConnections.replaceKey'
                    : 'remoteConnections.key',
                )
              "
              type="password"
              autocomplete="new-password"
              :disabled="!enabled"
            />
            <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{
              error
            }}</v-alert>
            <v-btn
              type="submit"
              variant="tonal"
              :loading="saving"
              :disabled="
                !enabled ||
                !form.name.trim() ||
                !form.base_url.trim() ||
                (!editing && !form.key)
              "
              >{{ t("core.common.save") }}</v-btn
            >
            <v-btn v-if="editing" variant="text" @click="reset">{{
              t("core.common.cancel")
            }}</v-btn>
          </v-form>
        </v-card-text>
        <v-card-actions
          ><v-btn variant="text" @click="$emit('refresh')">{{
            tm("remoteConnections.refresh")
          }}</v-btn
          ><v-spacer /><v-btn variant="text" @click="manage = false">{{
            t("core.common.close")
          }}</v-btn></v-card-actions
        >
      </v-card>
    </v-dialog>
  </section>
</template>
<script setup lang="ts">
import { reactive, ref, watch } from "vue";
import {
  remoteConnectionsApi,
  type RemoteConnection,
  type RemoteSession,
} from "@/api/remoteConnections";
import { accountSessionRevision } from "@/utils/accountSession";
import { useI18n, useModuleI18n } from "@/i18n/composables";
const props = defineProps<{
  enabled: boolean;
  connections: RemoteConnection[];
  sessions: Record<string, RemoteSession[]>;
  status: Record<string, string>;
  selectedConnection?: string;
  selectedSession?: string;
}>();
const emit = defineEmits<{
  select: [connection: string, session: string];
  refresh: [];
}>();
const { tm } = useModuleI18n("features/chat");
const { t } = useI18n();
const manage = ref(false),
  saving = ref(false),
  editing = ref(""),
  error = ref("");
const form = reactive({ name: "", base_url: "", key: "" });
function groups(id: string) {
  const result = new Map<string, RemoteSession[]>();
  for (const session of props.sessions[id] || []) {
    const key = session.project_id || "";
    result.set(key, [...(result.get(key) || []), session]);
  }
  return result;
}
function reset() {
  editing.value = "";
  form.name = "";
  form.base_url = "";
  form.key = "";
  error.value = "";
}
function edit(connection: RemoteConnection) {
  reset();
  editing.value = connection.connection_id;
  form.name = connection.name;
  form.base_url = connection.base_url;
}
async function save() {
  saving.value = true;
  error.value = "";
  const revision = accountSessionRevision.value;
  try {
    await remoteConnectionsApi.save(
      {
        name: form.name,
        base_url: form.base_url,
        ...(form.key ? { key: form.key } : {}),
      },
      editing.value || undefined,
    );
    if (revision === accountSessionRevision.value) {
      reset();
      emit("refresh");
    }
  } catch {
    if (revision === accountSessionRevision.value)
      error.value = tm("remoteConnections.saveFailed");
  } finally {
    saving.value = false;
    form.key = "";
  }
}
async function remove(id: string) {
  saving.value = true;
  const revision = accountSessionRevision.value;
  try {
    await remoteConnectionsApi.remove(id);
    if (revision === accountSessionRevision.value) emit("refresh");
  } catch {
    if (revision === accountSessionRevision.value)
      error.value = tm("remoteConnections.saveFailed");
  } finally {
    saving.value = false;
  }
}
watch(manage, () => reset());
watch(
  accountSessionRevision,
  () => {
    manage.value = false;
    reset();
  },
  { flush: "sync" },
);
</script>
<style scoped>
.remote-connections {
  padding: 12px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
summary {
  cursor: pointer;
  padding: 6px 0;
  overflow-wrap: anywhere;
}
small {
  display: block;
  opacity: 0.65;
}
.remote-project {
  margin-left: 12px;
}
.remote-session {
  display: block;
  text-align: left;
  width: 100%;
  padding: 8px;
  border-radius: 8px;
  overflow-wrap: anywhere;
}
.remote-session:hover,
.remote-session.selected {
  background: rgba(var(--v-theme-primary), 0.12);
}
</style>

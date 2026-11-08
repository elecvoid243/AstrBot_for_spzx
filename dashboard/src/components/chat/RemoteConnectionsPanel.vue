<template>
  <section class="remote-connections">
    <header class="remote-connections__header">
      <span class="remote-connections__title">{{
        tm("remoteConnections.title")
      }}</span>
      <button
        type="button"
        class="remote-manage-link"
        @click="manage = true"
      >
        {{ tm("remoteConnections.manage") }}
      </button>
    </header>
    <p v-if="!enabled" class="remote-disabled-hint">
      {{ tm("remoteConnections.disabled") }}
    </p>
    <details
      v-for="connection in connections"
      :key="connection.connection_id"
      class="remote-conn"
      open
    >
      <summary class="remote-conn__summary">
        <v-icon class="remote-conn__chevron" size="14"
          >mdi-chevron-right</v-icon
        >
        <span class="remote-conn__body">
          <span class="remote-conn__name">{{ connection.name }}</span>
          <span
            class="remote-conn__status"
            :class="`is-${statusTone(connection.connection_id)}`"
            >{{ statusLabel(connection.connection_id) }}</span
          >
        </span>
      </summary>
      <template v-if="enabled && status[connection.connection_id] === 'ready'">
        <button
          type="button"
          class="remote-new-session"
          @click="openCreate(connection)"
        >
          <v-icon size="14">mdi-plus</v-icon>
          {{ tm("remoteConnections.newSession") }}
        </button>
        <details
          v-for="[projectId, rows] in groups(connection.connection_id)"
          :key="projectId"
          open
          class="remote-project"
        >
          <summary class="remote-project__summary">
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
    <v-dialog v-model="creating" max-width="480">
      <v-card
        class="rc-create"
        :class="{ 'rc-create--denied': !createAllowed }"
      >
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("remoteConnections.newSession")
        }}</v-card-title>
        <v-card-text class="rc-create__body">
          <div v-if="createConnection" class="rc-create__meta">
            <span v-if="createConnection.name" class="rc-create__chip">{{
              createConnection.name
            }}</span>
            <span class="rc-create__url">{{ createConnection.base_url }}</span>
          </div>

          <v-alert
            v-if="createError"
            type="error"
            variant="tonal"
            density="compact"
            rounded="lg"
            class="rc-create__error"
            >{{ createError }}</v-alert
          >

          <!-- 2026-11-08 (elecvoid243): the denial used to be a bare <p> with
               no margin, which glued it to the first field. It is a real
               notice block now, so the eye reads "blocked" before "form". -->
          <v-alert
            v-if="!createAllowed && !createLoading"
            type="warning"
            variant="tonal"
            density="compact"
            rounded="lg"
            class="rc-create__notice"
            >{{ tm("remoteConnections.creationDenied") }}</v-alert
          >

          <div class="rc-create__form">
            <div class="rc-create__field">
              <div class="rc-create__label">
                {{ tm("remoteConnections.sessionTitle") }}
              </div>
              <v-text-field
                v-model="createTitle"
                maxlength="255"
                variant="outlined"
                density="compact"
                hide-details
                :aria-label="tm('remoteConnections.sessionTitle')"
                :disabled="!createAllowed || saving"
              />
            </div>
            <div class="rc-create__field">
              <div class="rc-create__label">
                {{ tm("remoteConnections.project") }}
              </div>
              <v-select
                v-model="createProject"
                :items="[
                  { title: tm('remoteConnections.noProject'), project_id: null },
                  ...createProjectItems,
                ]"
                item-title="title"
                item-value="project_id"
                variant="outlined"
                density="compact"
                hide-details
                :aria-label="tm('remoteConnections.project')"
                :disabled="!createAllowed || saving"
              />
            </div>
          </div>

          <p class="rc-create__hint">
            {{ tm("remoteConnections.creationHint") }}
          </p>
        </v-card-text>
        <v-card-actions class="rc-create__actions">
          <v-spacer />
          <v-btn variant="text" :disabled="saving" @click="creating = false">{{
            t("core.common.cancel")
          }}</v-btn>
          <v-btn
            variant="tonal"
            :loading="saving || createLoading"
            :disabled="!createAllowed || !createTitle.trim()"
            @click="createSession"
            >{{ tm("remoteConnections.newSession") }}</v-btn
          >
        </v-card-actions>
      </v-card>
    </v-dialog>
    <v-dialog v-model="manage" max-width="640" scrollable>
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("remoteConnections.manage")
        }}</v-card-title>
        <v-card-text class="pa-6 pt-4">
          <div v-if="!enabled" class="rc-banner">
            {{ tm("remoteConnections.disabled") }}
          </div>

          <div v-if="connections.length" class="rc-list">
            <div
              v-for="connection in connections"
              :key="connection.connection_id"
              class="rc-row"
              :class="{
                'is-editing': editing === connection.connection_id,
              }"
            >
              <div class="rc-row__meta">
                <span class="rc-row__name">{{ connection.name }}</span>
                <span class="rc-row__url">{{ connection.base_url }}</span>
              </div>
              <div class="rc-row__actions">
                <v-btn
                  icon
                  size="small"
                  variant="text"
                  :title="t('core.common.edit')"
                  :aria-label="t('core.common.edit')"
                  :disabled="!enabled || saving"
                  @click="edit(connection)"
                >
                  <v-icon size="16">mdi-pencil-outline</v-icon>
                </v-btn>
                <v-btn
                  icon
                  size="small"
                  variant="text"
                  color="error"
                  :title="t('core.common.delete')"
                  :aria-label="t('core.common.delete')"
                  :disabled="!enabled || saving"
                  @click="remove(connection.connection_id)"
                >
                  <v-icon size="16">mdi-trash-can-outline</v-icon>
                </v-btn>
              </div>
            </div>
          </div>

          <div class="rc-rule"></div>

          <v-form @submit.prevent="save">
            <v-text-field
              v-model="form.name"
              :label="tm('remoteConnections.name')"
              maxlength="128"
              :disabled="!enabled"
              variant="outlined"
              density="comfortable"
              rounded="lg"
              hide-details
              class="mb-3"
            />
            <v-text-field
              v-model="form.base_url"
              :label="tm('remoteConnections.address')"
              placeholder="http://192.168.1.20:6185"
              :disabled="!enabled"
              variant="outlined"
              density="comfortable"
              rounded="lg"
              hide-details
              class="mb-3"
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
              variant="outlined"
              density="comfortable"
              rounded="lg"
              hide-details
              class="mb-4"
            />
            <div v-if="error" class="rc-banner rc-banner--error" role="alert">
              {{ error }}
            </div>
            <div class="rc-form__actions">
              <v-btn
                type="submit"
                color="primary"
                variant="flat"
                rounded="lg"
                :loading="saving"
                :disabled="
                  !enabled ||
                  !form.name.trim() ||
                  !form.base_url.trim() ||
                  (!editing && !form.key)
                "
                >{{ t("core.common.save") }}</v-btn
              >
              <v-btn v-if="editing" variant="text" rounded="lg" @click="reset">{{
                t("core.common.cancel")
              }}</v-btn>
            </div>
          </v-form>
        </v-card-text>
        <v-card-actions class="px-6 pb-4">
          <v-btn
            variant="text"
            rounded="lg"
            prepend-icon="mdi-refresh"
            @click="$emit('refresh')"
            >{{ tm("remoteConnections.refresh") }}</v-btn
          >
          <v-spacer />
          <v-btn variant="text" rounded="lg" @click="manage = false">{{
            t("core.common.close")
          }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>
<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
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
function statusKey(connectionId: string) {
  return props.enabled ? props.status[connectionId] || "loading" : "disabled";
}
function statusLabel(connectionId: string) {
  return tm(`remoteConnections.status.${statusKey(connectionId)}`);
}
function statusTone(connectionId: string) {
  const key = statusKey(connectionId);
  if (key === "ready") return "success";
  if (key === "loading" || key === "disabled") return "muted";
  return "warning";
}
const manage = ref(false),
  saving = ref(false),
  editing = ref(""),
  error = ref("");
const form = reactive({ name: "", base_url: "", key: "" });
const creating = ref(false),
  createAllowed = ref(false),
  createLoading = ref(false);
const createConnection = ref<RemoteConnection>();
const createTitle = ref(""),
  createProject = ref<string | null>(null),
  createError = ref("");
const createProjects = ref<{ project_id: string; title: string }[]>([]);
const createProjectItems = computed(() =>
  createProjects.value.map((project) => ({
    ...project,
    title:
      createProjects.value.filter((other) => other.title === project.title)
        .length > 1
        ? `${project.title} · ${project.project_id}`
        : project.title,
  })),
);

async function openCreate(connection: RemoteConnection) {
  createConnection.value = { ...connection };
  createTitle.value = "";
  createProject.value = null;
  createProjects.value = [];
  createError.value = "";
  createAllowed.value = false;
  creating.value = true;
  createLoading.value = true;
  const accountRevision = accountSessionRevision.value;
  try {
    const response = await remoteConnectionsApi.creation(
      connection.connection_id,
      connection.revision,
    );
    if (
      accountRevision !== accountSessionRevision.value ||
      !creating.value ||
      createConnection.value?.revision !== connection.revision
    )
      return;
    createAllowed.value = response.data.data.allowed;
    createProjects.value = response.data.data.projects;
  } catch {
    if (accountRevision === accountSessionRevision.value && creating.value)
      createError.value = tm("remoteConnections.saveFailed");
  } finally {
    if (accountRevision === accountSessionRevision.value)
      createLoading.value = false;
  }
}
async function createSession() {
  const connection = createConnection.value;
  if (!connection || !props.enabled || !createAllowed.value || saving.value)
    return;
  saving.value = true;
  createError.value = "";
  const accountRevision = accountSessionRevision.value;
  try {
    const response = await remoteConnectionsApi.createSession(
      connection.connection_id,
      connection.revision,
      createTitle.value.trim(),
      createProject.value,
    );
    if (
      accountRevision !== accountSessionRevision.value ||
      !props.enabled ||
      !props.connections.some(
        (c) =>
          c.connection_id === connection.connection_id &&
          c.revision === connection.revision,
      )
    )
      return;
    creating.value = false;
    emit("refresh");
    emit("select", connection.connection_id, response.data.data.session_id);
  } catch {
    if (accountRevision === accountSessionRevision.value) {
      // A lost response may follow successful creation; inspect the refreshed list.
      createAllowed.value = false;
      createError.value = tm("remoteConnections.creationUnknown");
      emit("refresh");
    }
  } finally {
    saving.value = false;
  }
}
watch(
  () => [
    props.enabled,
    ...props.connections.map((c) => c.connection_id + c.revision),
  ],
  () => {
    if (
      !props.enabled ||
      !props.connections.some(
        (c) =>
          c.connection_id === createConnection.value?.connection_id &&
          c.revision === createConnection.value?.revision,
      )
    )
      creating.value = false;
  },
);
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
    creating.value = false;
    createConnection.value = undefined;
    createProjects.value = [];
    reset();
  },
  { flush: "sync" },
);
</script>
<style scoped>
.remote-connections {
  padding: 10px 12px 12px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}

/* The section title shares the sidebar group scale with the "本机" header in
   Chat.vue (CSS vars set on .sidebar-content) so the two read as peers. */
.remote-connections__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-bottom: 8px;
}

.remote-connections__title {
  font-size: var(--sidebar-group-size, 14px);
  font-weight: var(--sidebar-group-weight, 600);
  letter-spacing: 0.01em;
  color: var(
    --sidebar-group-color,
    rgba(var(--v-theme-on-surface), 0.86)
  );
}

.remote-manage-link {
  flex: 0 0 auto;
  margin-right: -6px;
  padding: 2px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.remote-manage-link:hover {
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-on-surface), 0.05);
}

.remote-disabled-hint {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 1.55;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.remote-conn {
  padding: 2px 0;
}

.remote-conn + .remote-conn {
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.remote-conn__summary {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 0 -4px;
  padding: 6px 4px;
  border-radius: 8px;
  list-style: none;
  cursor: pointer;
}

.remote-conn__summary::-webkit-details-marker,
.remote-project__summary::-webkit-details-marker {
  display: none;
}

.remote-conn__summary:hover {
  background: rgba(var(--v-theme-on-surface), 0.05);
}

.remote-conn__chevron {
  flex: 0 0 auto;
  margin-top: 2px;
  color: rgba(var(--v-theme-on-surface), 0.4);
  transition: transform 0.15s ease;
}

.remote-conn[open] > .remote-conn__summary .remote-conn__chevron {
  transform: rotate(90deg);
}

.remote-conn__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.remote-conn__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.88);
}

.remote-conn__status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  line-height: 1.4;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.remote-conn__status::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.7;
}

.remote-conn__status.is-success {
  color: rgb(var(--v-theme-success));
}

.remote-conn__status.is-warning {
  color: rgb(var(--v-theme-warning));
}

.remote-new-session {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin: 2px 0 4px 20px;
  padding: 4px 8px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: rgb(var(--v-theme-primary));
  font: inherit;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
}

.remote-new-session:hover {
  background: rgba(var(--v-theme-primary), 0.09);
}

.remote-project {
  margin-left: 20px;
}

.remote-project__summary {
  padding: 4px 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11.5px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  cursor: pointer;
  list-style: none;
}

.remote-session {
  display: block;
  width: 100%;
  margin: 1px 0;
  padding: 6px 8px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.8);
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  overflow-wrap: anywhere;
}

.remote-session:hover {
  background: rgba(var(--v-theme-on-surface), 0.05);
}

.remote-session.selected {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
  font-weight: 500;
}

/* ── Manage dialog ─────────────────────────────────────────────── */
.rc-banner {
  margin-bottom: 14px;
  padding: 10px 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 10px;
  background: rgba(var(--v-theme-on-surface), 0.04);
  color: rgba(var(--v-theme-on-surface), 0.68);
  font-size: 12.5px;
  line-height: 1.55;
}

.rc-banner--error {
  border-color: rgba(var(--v-theme-error), 0.22);
  background: rgba(var(--v-theme-error), 0.07);
  color: rgb(var(--v-theme-error));
}

.rc-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 16px;
}

.rc-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 10px;
  background: rgba(var(--v-theme-on-surface), 0.02);
}

.rc-row.is-editing {
  border-color: rgba(var(--v-theme-primary), 0.4);
  background: rgba(var(--v-theme-primary), 0.05);
}

.rc-row__meta {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.rc-row__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
}

.rc-row__url {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11.5px;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.rc-row__actions {
  display: flex;
  flex: 0 0 auto;
  gap: 2px;
}

.rc-rule {
  height: 1px;
  margin: 4px 0 18px;
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.rc-form__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* ── Create-session dialog (2026-11-08, elecvoid243) ───────────────
   Title / meta / notice / form / hint / actions each own their vertical
   rhythm instead of relying on Vuetify's 16px stack. Fields carry their
   label outside the box: at this size the floating label read like an
   unfilled placeholder and collided with the denial line above it. */
.rc-create .rc-create__body {
  padding: 4px 24px 0;
}

.rc-create__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin-top: 4px;
}

.rc-create__chip {
  flex: none;
  padding: 0 6px;
  border-radius: 4px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.62);
  font-size: 10.5px;
  line-height: 16px;
}

.rc-create__url {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 11.5px;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.rc-create__error {
  margin-top: 14px;
  font-size: 12px;
}

.rc-create__notice {
  margin-top: 18px;
  font-size: 12px;
}

.rc-create__form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-top: 20px;
}

.rc-create__label {
  margin-bottom: 6px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.02em;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

/* A denied host stays readable instead of fading to 38%: the dialog still
   shows what would be filled in once the host allows creation. */
.rc-create--denied .rc-create__label {
  color: rgba(var(--v-theme-on-surface), 0.42);
}

.rc-create--denied :deep(.v-field) {
  background: rgba(var(--v-theme-on-surface), 0.03);
}

.rc-create__hint {
  margin: 16px 0 0;
  font-size: 11.5px;
  line-height: 1.65;
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.rc-create .rc-create__actions {
  min-height: 0;
  margin: 18px 24px 0;
  padding: 14px 0 18px;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
</style>

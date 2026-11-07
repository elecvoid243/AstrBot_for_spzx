<template>
  <div class="remote-access-panel">
    <div v-if="error" class="ra-banner ra-banner--error" role="alert">
      <v-icon size="16" class="ra-banner__icon">mdi-alert-circle-outline</v-icon>
      <span>{{ error }}</span>
    </div>

    <div v-if="!loaded" class="ra-loading">
      <v-progress-linear indeterminate rounded height="3" />
    </div>

    <template v-else>
      <div v-if="!enabled" class="ra-banner ra-banner--info">
        <v-icon size="16" class="ra-banner__icon">mdi-information-outline</v-icon>
        <span>{{ tm("remoteAccess.disabled") }}</span>
      </div>

      <!-- Exposure ------------------------------------------------------ -->
      <section class="ra-section">
        <header class="ra-section__head">
          <h3 class="ra-section__title">{{ tm("remoteAccess.exposure") }}</h3>
          <p class="ra-section__hint">{{ tm("remoteAccess.scopeHint") }}</p>
        </header>

        <div class="ra-card">
          <v-select
            v-model="mode"
            :items="modes"
            :aria-label="tm('remoteAccess.exposure')"
            :disabled="busy"
            variant="outlined"
            density="comfortable"
            rounded="lg"
            hide-details
            @update:model-value="onModeChange"
          />
          <v-autocomplete
            v-if="mode === 'selected'"
            v-model="selectedIds"
            :items="sessions"
            item-title="label"
            item-value="session_id"
            :aria-label="tm('remoteAccess.sessions')"
            :disabled="busy"
            multiple
            chips
            closable-chips
            variant="outlined"
            density="comfortable"
            rounded="lg"
            hide-details
            class="mt-3"
            @update:model-value="onSelectionChange"
          />
          <!-- Always mounted: the auto-save status must not shift the section
               below when it appears and fades. -->
          <div class="ra-card__foot">
            <v-progress-circular
              v-if="savingExposure"
              indeterminate
              size="12"
              width="1.5"
            />
            <span v-else class="ra-saved" role="status">
              <v-icon size="14">mdi-check</v-icon>
              {{ tm("remoteAccess.saved") }}
            </span>
          </div>
        </div>
      </section>

      <!-- Credentials --------------------------------------------------- -->
      <section class="ra-section">
        <header class="ra-section__head">
          <h3 class="ra-section__title">{{ tm("remoteAccess.credentials") }}</h3>
          <p class="ra-section__hint">{{ tm("remoteAccess.keyHint") }}</p>
        </header>

        <div class="ra-create">
          <v-text-field
            v-model="name"
            :label="tm('remoteAccess.name')"
            maxlength="128"
            :disabled="busy"
            variant="outlined"
            density="comfortable"
            rounded="lg"
            hide-details
            class="ra-create__field"
            @keydown.enter="onNameEnter"
          />
          <v-btn
            color="primary"
            variant="flat"
            rounded="lg"
            height="40"
            :disabled="busy || !name.trim()"
            @click="createCredential"
          >
            {{ tm("remoteAccess.create") }}
          </v-btn>
        </div>

        <div v-if="newKey" class="ra-keycard">
          <div class="ra-keycard__head">
            <v-icon size="16">mdi-check-circle-outline</v-icon>
            <span>{{ tm("remoteAccess.keyOnce") }}</span>
          </div>
          <code class="ra-keycard__value">{{ newKey }}</code>
          <div class="ra-keycard__actions">
            <button type="button" class="ra-link" @click="copyKey">
              <v-icon size="14">{{
                copied ? "mdi-check" : "mdi-content-copy"
              }}</v-icon>
              {{
                copied ? tm("remoteAccess.copied") : tm("remoteAccess.copy")
              }}
            </button>
            <button
              type="button"
              class="ra-link ra-link--muted"
              @click="newKey = ''"
            >
              {{ tm("remoteAccess.hide") }}
            </button>
          </div>
        </div>

        <ul v-if="visibleCredentials.length" class="ra-cred-list">
          <li
            v-for="credential in visibleCredentials"
            :key="credential.credential_id"
            class="ra-cred"
          >
            <span class="ra-cred__name">{{ credential.name }}</span>
            <button
              type="button"
              class="ra-cred__revoke"
              :disabled="busy"
              @click="revoke(credential.credential_id)"
            >
              {{ tm("remoteAccess.revoke") }}
            </button>
          </li>
        </ul>
        <div v-else class="ra-empty">
          <v-icon size="16">mdi-key-outline</v-icon>
          <span>{{ tm("remoteAccess.emptyKeys") }}</span>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { remoteChatApi } from "@/api/v1";
import { useModuleI18n } from "@/i18n/composables";

const { tm } = useModuleI18n("features/chat");
const enabled = ref(false);
const loaded = ref(false);
const busy = ref(false);
const savingExposure = ref(false);
const error = ref("");
const saved = ref(false);
const copied = ref(false);
const mode = ref<"all" | "selected">("selected");
const selectedIds = ref<string[]>([]);
const sessions = ref<Array<{ session_id: string; label: string }>>([]);
const credentials = ref<
  Array<{ credential_id: string; name: string; revoked: boolean }>
>([]);
const name = ref("");
const newKey = ref("");
const modes = computed(() => [
  { title: tm("remoteAccess.selected"), value: "selected" },
  { title: tm("remoteAccess.all"), value: "all" },
]);

// Deleted keys are revoked server-side (the record stays for audit); the
// panel treats them as gone: they never render, so the list matches what the
// user can still use.
const visibleCredentials = computed(() =>
  credentials.value.filter((credential) => !credential.revoked),
);

// A freshly rendered key gets a clean "copy" label, not the previous key's
// "copied" state.
watch(newKey, () => {
  copied.value = false;
});

async function load() {
  const response = await remoteChatApi.access();
  const data = response.data.data;
  enabled.value = data.enabled;
  mode.value = data.mode;
  selectedIds.value = data.session_ids;
  credentials.value = data.credentials;
  sessions.value = data.sessions.map((session: any) => ({
    session_id: session.session_id,
    label: [session.project_name, session.display_name || session.session_id]
      .filter(Boolean)
      .join(" / "),
  }));
  loaded.value = true;
}

// Scope changes save immediately: the dialog no longer carries a Save button.
// A sequence guard keeps a slow response from an older selection from
// overwriting the outcome of the latest one.
let exposureSaveSeq = 0;
let savedTimer: number | null = null;

// The emitted value is passed explicitly: v-model's own update and this
// listener share one event, and the saved payload must not depend on which
// listener runs first.
function onModeChange(value: unknown) {
  const next = value === "all" ? "all" : "selected";
  void saveExposure(next, next === "all" ? [] : selectedIds.value);
}

function onSelectionChange(value: unknown) {
  const ids = Array.isArray(value) ? value.map(String) : [];
  void saveExposure(mode.value === "all" ? "all" : "selected", ids);
}

async function saveExposure(
  nextMode: "all" | "selected" = mode.value,
  ids: string[] = selectedIds.value,
) {
  const seq = ++exposureSaveSeq;
  savingExposure.value = true;
  error.value = "";
  saved.value = false;
  if (savedTimer !== null) {
    window.clearTimeout(savedTimer);
    savedTimer = null;
  }
  try {
    await remoteChatApi.setExposure(nextMode, nextMode === "all" ? [] : ids);
    if (seq !== exposureSaveSeq) return;
    saved.value = true;
    savedTimer = window.setTimeout(() => {
      saved.value = false;
      savedTimer = null;
    }, 2200);
  } catch (err: any) {
    if (seq === exposureSaveSeq) {
      error.value = err?.response?.data?.message || String(err);
    }
  } finally {
    if (seq === exposureSaveSeq) savingExposure.value = false;
  }
}

async function createCredential() {
  if (!name.value.trim() || busy.value) return;
  busy.value = true;
  error.value = "";
  newKey.value = "";
  try {
    const response = await remoteChatApi.createCredential(name.value.trim());
    newKey.value = response.data.data.key;
    name.value = "";
    await load();
  } catch (err: any) {
    error.value = err?.response?.data?.message || String(err);
  } finally {
    busy.value = false;
  }
}

// Enter submits the binding name, but an IME commit must not create a key.
function onNameEnter(event: KeyboardEvent) {
  if (event.isComposing) return;
  void createCredential();
}

async function revoke(id: string) {
  busy.value = true;
  error.value = "";
  newKey.value = "";
  try {
    await remoteChatApi.revokeCredential(id);
    await load();
  } catch (err: any) {
    error.value = err?.response?.data?.message || String(err);
  } finally {
    busy.value = false;
  }
}

async function copyKey() {
  try {
    await navigator.clipboard.writeText(newKey.value);
    copied.value = true;
    window.setTimeout(() => {
      copied.value = false;
    }, 1600);
  } catch (err: any) {
    error.value = String(err);
  }
}

onMounted(async () => {
  busy.value = true;
  try {
    await load();
  } catch (err: any) {
    error.value = err?.response?.data?.message || String(err);
  } finally {
    busy.value = false;
  }
});

onBeforeUnmount(() => {
  if (savedTimer !== null) window.clearTimeout(savedTimer);
});
</script>

<style scoped>
.remote-access-panel {
  font-size: 13px;
  color: rgb(var(--v-theme-on-surface));
}

.ra-loading {
  padding: 24px 0;
}

.ra-banner {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 16px;
  padding: 10px 12px;
  border-radius: 10px;
  font-size: 12.5px;
  line-height: 1.55;
}

.ra-banner__icon {
  flex: 0 0 auto;
  margin-top: 1px;
}

.ra-banner--error {
  color: rgb(var(--v-theme-error));
  border: 1px solid rgba(var(--v-theme-error), 0.22);
  background: rgba(var(--v-theme-error), 0.07);
}

.ra-banner--info {
  color: rgba(var(--v-theme-on-surface), 0.7);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.ra-section + .ra-section {
  margin-top: 24px;
  padding-top: 22px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.ra-section__head {
  margin-bottom: 12px;
}

.ra-section__title {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.ra-section__hint {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.ra-card {
  padding: 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 12px;
  background: rgba(var(--v-theme-on-surface), 0.025);
}

.ra-card__foot {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
  min-height: 16px;
}

.ra-saved {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: rgb(var(--v-theme-success));
}

.ra-create {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ra-create__field {
  flex: 1 1 auto;
  min-width: 0;
}

.ra-keycard {
  margin-top: 12px;
  padding: 12px;
  border: 1px solid rgba(var(--v-theme-success), 0.26);
  border-radius: 12px;
  background: rgba(var(--v-theme-success), 0.07);
}

.ra-keycard__head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  font-weight: 600;
  color: rgb(var(--v-theme-success));
}

.ra-keycard__value {
  display: block;
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.05);
  color: rgba(var(--v-theme-on-surface), 0.9);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  overflow-wrap: anywhere;
  user-select: all;
}

.ra-keycard__actions {
  display: flex;
  gap: 16px;
  margin-top: 10px;
}

.ra-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 0;
  border: 0;
  background: transparent;
  color: rgb(var(--v-theme-primary));
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.ra-link:hover {
  text-decoration: underline;
}

.ra-link--muted {
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.ra-link:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
  border-radius: 4px;
}

.ra-cred-list {
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
  overflow: hidden;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 12px;
}

.ra-cred {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
}

.ra-cred + .ra-cred {
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.ra-cred__name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.ra-cred__revoke {
  flex: 0 0 auto;
  padding: 4px 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.ra-cred__revoke:hover {
  color: rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.08);
}

.ra-cred__revoke:focus-visible {
  outline: 2px solid rgb(var(--v-theme-error));
  outline-offset: 1px;
}

.ra-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 12px;
  padding: 16px 12px;
  border: 1px dashed rgba(var(--v-theme-on-surface), 0.16);
  border-radius: 12px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 12px;
}

@media (max-width: 520px) {
  .ra-create {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>

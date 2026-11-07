<template>
  <div class="remote-access-panel">
    <h3 v-if="systemOnly" class="mb-3">{{ tm("remoteAccess.title") }}</h3>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{
      error
    }}</v-alert>
    <v-progress-linear v-if="busy" indeterminate class="mb-3" />
    <template v-if="loaded">
      <template v-if="systemOnly">
        <v-switch
          :model-value="enabled"
          :label="tm('remoteAccess.enabled')"
          :disabled="busy"
          color="primary"
          hide-details
          @update:model-value="setEnabled(Boolean($event))"
        />
        <p class="text-body-2">{{ tm("remoteAccess.enabledHint") }}</p>
      </template>
      <template v-else>
        <v-alert v-if="!enabled" type="info" variant="tonal" class="mb-3">
          {{ tm("remoteAccess.disabled") }}
        </v-alert>
        <p class="text-body-2 mb-3">{{ tm("remoteAccess.scopeHint") }}</p>
        <v-select
          v-model="mode"
          :items="modes"
          :label="tm('remoteAccess.exposure')"
          :disabled="busy"
          variant="outlined"
          hide-details
          class="mb-3"
        />
        <v-autocomplete
          v-if="mode === 'selected'"
          v-model="selectedIds"
          :items="sessions"
          item-title="label"
          item-value="session_id"
          :label="tm('remoteAccess.sessions')"
          :disabled="busy"
          multiple
          chips
          closable-chips
          variant="outlined"
          class="mb-2"
        />
        <v-btn variant="tonal" :disabled="busy" @click="saveExposure">
          {{ tm("remoteAccess.save") }}
        </v-btn>
        <span v-if="saved" role="status" class="ml-3">{{
          tm("remoteAccess.saved")
        }}</span>
        <h4 class="mt-6 mb-2">{{ tm("remoteAccess.credentials") }}</h4>
        <p class="text-body-2 mb-3">{{ tm("remoteAccess.keyHint") }}</p>
        <v-text-field
          v-model="name"
          :label="tm('remoteAccess.name')"
          maxlength="128"
          :disabled="busy"
          variant="outlined"
          hide-details
          class="mb-3"
        />
        <v-btn
          variant="tonal"
          :disabled="busy || !name.trim()"
          @click="createCredential"
        >
          {{ tm("remoteAccess.create") }}
        </v-btn>
        <v-alert v-if="newKey" type="success" variant="tonal" class="mt-3">
          <p>{{ tm("remoteAccess.keyOnce") }}</p>
          <code class="remote-key">{{ newKey }}</code>
          <v-btn variant="text" @click="copyKey">{{
            tm("remoteAccess.copy")
          }}</v-btn>
          <v-btn variant="text" @click="newKey = ''">{{
            tm("remoteAccess.hide")
          }}</v-btn>
        </v-alert>
        <v-list v-if="credentials.length" class="mt-3">
          <v-list-item
            v-for="credential in credentials"
            :key="credential.credential_id"
          >
            <v-list-item-title>{{ credential.name }}</v-list-item-title>
            <template #append>
              <span v-if="credential.revoked">{{
                tm("remoteAccess.revoked")
              }}</span>
              <v-btn
                v-else
                variant="text"
                color="error"
                :disabled="busy"
                @click="revoke(credential.credential_id)"
              >
                {{ tm("remoteAccess.revoke") }}
              </v-btn>
            </template>
          </v-list-item>
        </v-list>
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { remoteChatApi } from "@/api/v1";
import { useModuleI18n } from "@/i18n/composables";

defineProps<{ systemOnly?: boolean }>();
const { tm } = useModuleI18n("features/chat");
const enabled = ref(false);
const loaded = ref(false);
const busy = ref(false);
const error = ref("");
const saved = ref(false);
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

async function saveExposure() {
  busy.value = true;
  error.value = "";
  saved.value = false;
  try {
    await remoteChatApi.setExposure(
      mode.value,
      mode.value === "all" ? [] : selectedIds.value,
    );
    saved.value = true;
  } catch (err: any) {
    error.value = err?.response?.data?.message || String(err);
  } finally {
    busy.value = false;
  }
}

async function createCredential() {
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

async function setEnabled(value: boolean) {
  busy.value = true;
  error.value = "";
  try {
    await remoteChatApi.setEnabled(value);
    enabled.value = value;
  } catch (err: any) {
    error.value = err?.response?.data?.message || String(err);
  } finally {
    busy.value = false;
  }
}

async function copyKey() {
  try {
    await navigator.clipboard.writeText(newKey.value);
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
</script>

<style scoped>
.remote-key {
  display: block;
  overflow-wrap: anywhere;
  user-select: all;
  padding-block: 12px;
}
</style>

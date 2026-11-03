<template>
  <v-dialog
    class="workbench-dialog"
    scrollable
    :model-value="modelValue"
    max-width="760"
    :persistent="busy"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
        tm("chatAccess.title")
      }}</v-card-title>
      <v-card-text class="chat-access-body">
        <h3 class="text-h6 mb-3">
          {{ asset.definition.name }} · v{{ asset.version }}
        </h3>
        <p class="mb-3">{{ tm("chatAccess.scope") }}</p>
        <v-alert
          v-if="error"
          type="error"
          variant="tonal"
          class="mb-3"
          role="alert"
          >{{ error }}</v-alert
        >
        <p class="text-body-2 mb-3">{{ tm("chatAccess.identityHelp") }}</p>
        <v-textarea
          v-model="identityText"
          :label="tm('chatAccess.identity')"
          :disabled="busy"
          data-testid="identity-text"
        />
        <v-alert
          v-if="identityText && !identity"
          type="error"
          variant="tonal"
          role="alert"
          >{{ tm("chatAccess.invalidIdentity") }}</v-alert
        >
        <template v-if="identity">
          <v-text-field
            :model-value="identity.platform_id"
            :label="tm('chatAccess.platform')"
            readonly
          />
          <v-text-field
            :model-value="identity.sender_id"
            :label="tm('chatAccess.sender')"
            readonly
          />
          <v-text-field
            :model-value="identity.source"
            :label="tm('chatAccess.source')"
            readonly
          />
          <v-text-field
            :model-value="identity.profile_id"
            :label="tm('chatAccess.profile')"
            readonly
          />
        </template>
        <v-btn
          variant="tonal"
          color="primary"
          :loading="busy"
          :disabled="!identity"
          data-testid="save-chat-access"
          @click="save"
          >{{ tm("chatAccess.save") }}</v-btn
        >
        <p class="text-body-2 mt-4">{{ tm("chatAccess.invoke") }}</p>
        <code class="chat-access-command"
          >/asset {{ asset.asset_id }} | {"input":"value"}</code
        >
        <h4 class="text-subtitle-1 mt-5 mb-2">
          {{ tm("chatAccess.existing") }}
        </h4>
        <p v-if="!grants.length" class="text-body-2">
          {{ tm("chatAccess.empty") }}
        </p>
        <v-card
          v-for="grant in grants"
          :key="`${grant.platform_id}:${grant.sender_id}`"
          variant="outlined"
          class="mb-2"
        >
          <v-card-text>
            <strong
              >{{ grant.platform_id }} · {{ grant.sender_id }} · v{{
                grant.version
              }}</strong
            >
            <p>{{ grant.source }}</p>
            <p>{{ tm("chatAccess.profile") }}: {{ grant.profile_id }}</p>
            <v-btn
              variant="text"
              color="error"
              :disabled="busy"
              data-testid="revoke-chat-access"
              @click="revoke(grant)"
              >{{ tm("chatAccess.revoke") }}</v-btn
            >
          </v-card-text>
        </v-card>
      </v-card-text>
      <v-card-actions
        ><v-spacer /><v-btn
          variant="text"
          :disabled="busy"
          @click="emit('update:modelValue', false)"
          >{{ tm("v2.close") }}</v-btn
        ></v-card-actions
      >
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { workflowAssetsApi } from "@/api/workflowAssets";
import type { ChatAccessRequest } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";

const props = defineProps<{
  modelValue: boolean;
  asset: { asset_id: string; version: number; definition: { name: string } };
}>();
const emit = defineEmits<{
  (event: "update:modelValue", value: boolean): void;
}>();
const { tm } = useModuleI18n("features/agent-teams");
const identityText = ref("");
const identity = computed(() => {
  try {
    const value = JSON.parse(identityText.value);
    const limits = {
      platform_id: 128,
      sender_id: 256,
      source: 512,
      profile_id: 128,
    };
    if (
      !value ||
      typeof value !== "object" ||
      Array.isArray(value) ||
      Object.keys(value).length !== 4 ||
      !Object.entries(limits).every(
        ([key, max]) =>
          typeof value[key] === "string" &&
          value[key].trim() === value[key] &&
          value[key].length > 0 &&
          value[key].length <= max,
      )
    )
      return null;
    const match =
      /^([^:]+):(FriendMessage|GroupMessage|OtherMessage):(.+)$/.exec(
        value.source,
      );
    if (!match || match[1] !== value.platform_id || !match[3].trim())
      return null;
    return value as Pick<
      ChatAccessRequest,
      "platform_id" | "sender_id" | "source" | "profile_id"
    >;
  } catch {
    return null;
  }
});
const grants = ref<ChatAccessRequest[]>([]);
const busy = ref(false);
const error = ref("");

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return;
    busy.value = true;
    error.value = "";
    try {
      grants.value = (
        await workflowAssetsApi.chatAccess(props.asset.asset_id)
      ).grants;
    } catch (cause) {
      error.value = extractApiError(
        cause,
        tm("errors.operationFailed"),
      ).message;
    } finally {
      busy.value = false;
    }
  },
  { immediate: true },
);

async function save() {
  if (!identity.value) return;
  busy.value = true;
  error.value = "";
  try {
    await workflowAssetsApi.saveChatAccess(props.asset.asset_id, {
      ...identity.value,
      version: props.asset.version,
    });
    grants.value = (
      await workflowAssetsApi.chatAccess(props.asset.asset_id)
    ).grants;
  } catch (cause) {
    error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    busy.value = false;
  }
}

async function revoke(grant: ChatAccessRequest) {
  busy.value = true;
  error.value = "";
  try {
    await workflowAssetsApi.revokeChatAccess(
      props.asset.asset_id,
      grant.platform_id,
      grant.sender_id,
    );
    grants.value = (
      await workflowAssetsApi.chatAccess(props.asset.asset_id)
    ).grants;
  } catch (cause) {
    error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.chat-access-body {
  overflow-wrap: anywhere;
}
.chat-access-command {
  display: block;
  white-space: pre-wrap;
}
</style>

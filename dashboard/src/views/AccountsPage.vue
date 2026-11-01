<script setup lang="ts">
import { onMounted, ref } from "vue";
import { accountApi } from "@/api/v1";
import { useModuleI18n } from "@/i18n/composables";
import type { AccountInfo } from "@/api/generated/openapi-v1";
const { tm } = useModuleI18n("features/auth");
const accounts = ref<AccountInfo[]>([]);
const username = ref("");
const password = ref("");
const dialog = ref(false);
const saving = ref(false);
const error = ref("");
const formAccount = ref<AccountInfo | null>(null);
const disableTarget = ref<AccountInfo | null>(null);
const notice = ref("");
onMounted(async () => {
  try {
    accounts.value = (await accountApi.list()).data.data;
  } catch (reason) {
    error.value = String(reason);
  }
});
async function saveAccount() {
  saving.value = true;
  error.value = "";
  try {
    if (formAccount.value) {
      await accountApi.resetPassword(
        formAccount.value.account_id,
        password.value,
      );
      notice.value = tm("accounts.passwordReset");
    } else {
      await accountApi.create({
        username: username.value,
        password: password.value,
      });
      notice.value = "";
    }
    password.value = "";
    username.value = "";
    dialog.value = false;
    accounts.value = (await accountApi.list()).data.data;
  } catch (reason: any) {
    error.value = reason.response?.data?.message || String(reason);
  } finally {
    saving.value = false;
  }
}
async function disableAccount() {
  if (!disableTarget.value) return;
  saving.value = true;
  error.value = "";
  try {
    await accountApi.disable(disableTarget.value.account_id);
    disableTarget.value = null;
    notice.value = tm("accounts.accountDisabled");
    accounts.value = (await accountApi.list()).data.data;
  } catch (reason: any) {
    error.value = reason.response?.data?.message || String(reason);
  } finally {
    saving.value = false;
  }
}
</script>
<template>
  <v-card class="pa-6">
    <div class="d-flex align-center justify-space-between mb-4">
      <h1 class="text-h3">{{ tm("accounts.title") }}</h1>
      <v-btn
        variant="tonal"
        @click="
          dialog = true;
          error = '';
          formAccount = null;
          username = '';
          password = '';
        "
        >{{ tm("accounts.create") }}</v-btn
      >
    </div>
    <v-alert v-if="notice" type="success" variant="tonal" class="mb-4">{{
      notice
    }}</v-alert>
    <v-alert
      v-if="error && !dialog && !disableTarget"
      type="error"
      variant="tonal"
      >{{ error }}</v-alert
    >
    <v-table
      ><thead>
        <tr>
          <th>{{ tm("username") }}</th>
          <th>{{ tm("accounts.status") }}</th>
          <th>{{ tm("accounts.actions") }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="account in accounts" :key="account.account_id">
          <td>{{ account.username }}</td>
          <td>
            {{
              account.enabled ? tm("accounts.enabled") : tm("accounts.disabled")
            }}
          </td>
          <td>
            <v-btn
              variant="text"
              :disabled="saving"
              @click="
                formAccount = account;
                password = '';
                error = '';
                dialog = true;
              "
            >
              {{ tm("accounts.resetPassword") }}
            </v-btn>
            <v-btn
              variant="text"
              color="error"
              :disabled="saving || !account.enabled"
              @click="
                disableTarget = account;
                error = '';
              "
            >
              {{ tm("accounts.disable") }}
            </v-btn>
          </td>
        </tr>
      </tbody>
    </v-table>
    <v-dialog
      v-model="dialog"
      max-width="480"
      :persistent="saving"
      @after-leave="password = ''"
    >
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          formAccount ? tm("accounts.resetPassword") : tm("accounts.create")
        }}</v-card-title>
        <v-form @submit.prevent="saveAccount">
          <v-card-text>
            <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{
              error
            }}</v-alert>
            <v-text-field
              v-if="!formAccount"
              v-model="username"
              :label="tm('username')"
              autocomplete="off"
              required
              minlength="3"
              maxlength="128"
            />
            <p v-else class="mb-4">
              {{ formAccount.username }}: {{ tm("accounts.resetWarning") }}
            </p>
            <v-text-field
              v-model="password"
              :label="
                formAccount
                  ? tm('accounts.newPassword')
                  : tm('accounts.initialPassword')
              "
              type="password"
              autocomplete="new-password"
              required
            />
            <p>{{ tm("setup.passwordHint") }}</p>
          </v-card-text>
          <v-card-actions
            ><v-spacer /><v-btn
              variant="text"
              :disabled="saving"
              @click="dialog = false"
              >{{ tm("accounts.cancel") }}</v-btn
            >
            <v-btn
              type="submit"
              variant="tonal"
              :loading="saving"
              :disabled="
                saving || (!formAccount && !username.trim()) || !password
              "
              >{{
                formAccount
                  ? tm("accounts.resetPassword")
                  : tm("accounts.create")
              }}</v-btn
            ></v-card-actions
          >
        </v-form>
      </v-card>
    </v-dialog>
    <v-dialog
      :model-value="!!disableTarget"
      max-width="480"
      :persistent="saving"
      @update:model-value="!$event && (disableTarget = null)"
    >
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("accounts.disable")
        }}</v-card-title>
        <v-card-text>
          <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{
            error
          }}</v-alert>
          <p>
            {{ disableTarget?.username }}: {{ tm("accounts.disableWarning") }}
          </p>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn
            variant="text"
            :disabled="saving"
            @click="disableTarget = null"
            >{{ tm("accounts.cancel") }}</v-btn
          >
          <v-btn
            variant="tonal"
            color="error"
            :loading="saving"
            :disabled="saving"
            @click="disableAccount"
            >{{ tm("accounts.disable") }}</v-btn
          >
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { accountApi } from "@/api/v1";
import { useModuleI18n } from "@/i18n/composables";
const { tm } = useModuleI18n("features/auth");
const accounts = ref<
  Array<{ account_id: string; username: string; enabled: boolean }>
>([]);
const username = ref("");
const password = ref("");
const dialog = ref(false);
const saving = ref(false);
const error = ref("");
onMounted(async () => {
  try {
    accounts.value = (await accountApi.list()).data.data;
  } catch (reason) {
    error.value = String(reason);
  }
});
async function create() {
  saving.value = true;
  error.value = "";
  try {
    await accountApi.create({
      username: username.value,
      password: password.value,
    });
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
        "
        >{{ tm("accounts.create") }}</v-btn
      >
    </div>
    <v-alert v-if="error && !dialog" type="error" variant="tonal">{{
      error
    }}</v-alert>
    <v-table
      ><thead>
        <tr>
          <th>{{ tm("username") }}</th>
          <th>{{ tm("accounts.status") }}</th>
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
        </tr>
      </tbody>
    </v-table>
    <v-dialog v-model="dialog" max-width="480">
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("accounts.create")
        }}</v-card-title>
        <v-form @submit.prevent="create">
          <v-card-text>
            <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{
              error
            }}</v-alert>
            <v-text-field
              v-model="username"
              :label="tm('username')"
              autocomplete="off"
              required
              minlength="3"
              maxlength="128"
            />
            <v-text-field
              v-model="password"
              :label="tm('accounts.initialPassword')"
              type="password"
              autocomplete="new-password"
              required
            />
            <p>{{ tm("setup.passwordHint") }}</p>
          </v-card-text>
          <v-card-actions
            ><v-spacer /><v-btn variant="text" @click="dialog = false">{{
              tm("accounts.cancel")
            }}</v-btn>
            <v-btn
              type="submit"
              variant="tonal"
              :loading="saving"
              :disabled="!username.trim() || !password"
              >{{ tm("accounts.create") }}</v-btn
            ></v-card-actions
          >
        </v-form>
      </v-card>
    </v-dialog>
  </v-card>
</template>

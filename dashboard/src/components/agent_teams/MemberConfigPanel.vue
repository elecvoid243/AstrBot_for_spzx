<template>
  <section class="member-config-panel" :aria-label="tm('memberConfig.title')">
    <div class="member-config-header">
      <h3 class="member-config-title">{{ tm('memberConfig.title') }}</h3>
    </div>
    <div class="member-config-body">
      <!-- ① Member list: click a row to load its config into the form. -->
      <aside class="member-config-list" :aria-label="tm('memberConfig.listTitle')">
        <span class="member-config-list-title">{{ tm('memberConfig.listTitle') }}</span>
        <p class="member-config-hint">{{ tm('memberConfig.selectHint') }}</p>
        <button
          v-for="m in team?.members ?? []"
          :key="m.member_id"
          type="button"
          class="member-config-item"
          :class="{ 'is-selected': m.member_id === selectedMemberId }"
          :aria-pressed="m.member_id === selectedMemberId"
          @click="selectedMemberId = m.member_id"
        >
          <i
            class="member-config-dot"
            :style="{ background: collabMemberColor(String(m.name ?? m.member_id)) }"
            aria-hidden="true"
          />
          <span class="member-config-name">{{ m.name ?? m.member_id }}</span>
          <span v-if="m.member_id === team?.coordinator_member_id" class="member-config-coord">
            {{ tm('teams.coordinator') }}
          </span>
        </button>
      </aside>

      <!-- ② Config form for the selected member. -->
      <div class="member-config-form-wrap">
        <MemberConfigForm v-if="selectedMember" :member="selectedMember" :team="team" @saved="onSaved" />
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import MemberConfigForm from './MemberConfigForm.vue';
import { useModuleI18n } from '@/i18n/composables';
import { collabMemberColor } from '@/utils/memberColors';

const props = defineProps<{
  /** The team whose members are edited (rows carry member_id/name/...). */
  team: any;
}>();
const emit = defineEmits<{
  (e: 'updateTeam', team: any): void;
}>();

const { tm } = useModuleI18n('features/agent-teams');

const selectedMemberId = ref<string>('');

const selectedMember = computed(
  () =>
    (props.team?.members ?? []).find(
      (m: any) => String(m.member_id) === selectedMemberId.value,
    ) ?? null,
);

// Keep a valid selection: default to the first member, and fall back if the
// team changes and the selected row no longer exists (e.g. after a refresh).
watch(
  () => props.team,
  () => {
    const members = props.team?.members ?? [];
    if (!members.some((m: any) => String(m.member_id) === selectedMemberId.value)) {
      selectedMemberId.value = members[0] ? String(members[0].member_id) : '';
    }
  },
  { immediate: true },
);

/** Re-emit the resolved team so the page can refresh its state after save. */
function onSaved(team: any) {
  emit('updateTeam', team);
}
</script>

<style scoped>
.member-config-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.member-config-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.member-config-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.member-config-body {
  display: flex;
  align-items: flex-start;
  gap: 16px;
}

.member-config-list {
  width: 220px;
  flex: none;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px;
  border: 1px solid var(--dashboard-border, rgba(128, 128, 128, 0.25));
  border-radius: 12px;
  max-height: 560px;
  overflow-y: auto;
}

.member-config-list-title {
  font-size: 13px;
  font-weight: 600;
}

.member-config-hint {
  margin: 0;
  font-size: 11px;
  color: var(--dashboard-muted, rgba(128, 128, 128, 0.8));
}

.member-config-item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.member-config-item:hover {
  background: rgba(128, 128, 128, 0.12);
  border-color: var(--dashboard-border, rgba(128, 128, 128, 0.25));
}

.member-config-item.is-selected {
  background: rgba(128, 128, 128, 0.16);
  border-color: var(--dashboard-border, rgba(128, 128, 128, 0.4));
}

.member-config-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex: none;
}

.member-config-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 600;
}

.member-config-coord {
  flex: none;
  padding: 0 4px;
  border-radius: 4px;
  background: rgba(128, 128, 128, 0.15);
  font-size: 10px;
}

.member-config-form-wrap {
  flex: 1;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--dashboard-border, rgba(128, 128, 128, 0.25));
  border-radius: 12px;
}

@media (max-width: 900px) {
  .member-config-body {
    flex-direction: column;
  }

  .member-config-list {
    width: 100%;
    max-height: none;
    flex-direction: row;
    flex-wrap: wrap;
  }

  .member-config-list-title,
  .member-config-hint {
    width: 100%;
  }

  .member-config-item {
    width: auto;
  }
}
</style>

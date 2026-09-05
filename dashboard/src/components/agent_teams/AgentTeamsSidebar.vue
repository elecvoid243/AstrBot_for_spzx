<template>
  <aside class="agent-teams-sidebar">
    <v-btn
      variant="tonal"
      color="primary"
      prepend-icon="mdi-plus"
      block
      @click="emit('create')"
    >
      {{ tm('teams.create') }}
    </v-btn>

    <div v-if="teams.length === 0" class="teams-empty">
      {{ tm('teams.empty') }}
    </div>

    <div class="team-list">
      <div
        v-for="team in teams"
        :key="team.team_id"
        class="team-row"
        :class="{ 'is-selected': team.team_id === selectedTeamId }"
        @click="emit('select', team.team_id)"
      >
        <span class="team-name">{{ team.name }}</span>
        <!-- Edit entry for the selected team: opens the team dialog in edit
             mode (name/coordinator/config). .stop keeps the row select. -->
        <v-btn
          v-if="team.team_id === selectedTeamId"
          icon="mdi-pencil"
          variant="text"
          size="small"
          class="team-edit"
          :aria-label="tm('teams.edit')"
          @click.stop="emit('edit')"
        />
        <span class="team-count">{{ team.members?.length ?? 0 }}</span>
      </div>
    </div>

    <template v-if="selectedTeam">
      <div class="members-head">
        <span>{{ tm('members.title') }}</span>
        <v-btn
          icon="mdi-plus"
          variant="text"
          size="small"
          class="member-add"
          :aria-label="tm('members.add')"
          @click="emit('addMember')"
        />
      </div>
      <div
        v-for="member in selectedTeam.members"
        :key="member.member_id"
        class="member-row"
        :class="{
          'is-coordinator': member.member_id === selectedTeam.coordinator_member_id,
        }"
      >
        <span class="member-name">{{ member.name }}</span>
        <v-chip
          v-if="member.member_id === selectedTeam.coordinator_member_id"
          size="x-small"
          color="amber-darken-2"
          variant="tonal"
          class="coordinator-chip"
        >
          <v-icon start size="12">mdi-crown</v-icon>
          {{ tm('teams.coordinator') }}
        </v-chip>
        <v-tooltip location="bottom">
          <template #activator="{ props: tooltipProps }">
            <v-btn
              icon="mdi-close"
              variant="text"
              size="small"
              color="error"
              class="member-remove"
              :aria-label="tm('members.remove')"
              v-bind="tooltipProps"
              @click="emit('removeMember', member.member_id)"
            />
          </template>
          {{ tm('members.remove') }}
        </v-tooltip>
      </div>
    </template>
  </aside>
</template>

<script setup lang="ts">
import { useModuleI18n } from '@/i18n/composables';

defineProps<{
  teams: any[];
  selectedTeamId: string | null;
  selectedTeam: any | null;
}>();

const emit = defineEmits<{
  (e: 'select', teamId: string): void;
  (e: 'create'): void;
  (e: 'edit'): void;
  (e: 'addMember'): void;
  (e: 'removeMember', memberId: string): void;
}>();

const { tm } = useModuleI18n('features/agent-teams');
</script>

<style scoped>
.agent-teams-sidebar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.teams-empty {
  padding: 12px 4px;
  color: var(--dashboard-muted, rgba(0, 0, 0, 0.55));
  font-size: 13px;
  line-height: 1.6;
}

.team-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.team-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--dashboard-border, rgba(0, 0, 0, 0.08));
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.team-row:hover {
  background: rgba(var(--v-theme-primary), 0.06);
}

.team-row.is-selected {
  background: rgba(var(--v-theme-primary), 0.12);
  border-color: rgba(var(--v-theme-primary), 0.4);
}

.team-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  font-size: 14px;
}

.team-edit {
  flex-shrink: 0;
}

.team-count {
  flex-shrink: 0;
  color: var(--dashboard-muted, rgba(0, 0, 0, 0.55));
  font-size: 12px;
}

.members-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
  padding: 0 4px;
  color: var(--dashboard-muted, rgba(0, 0, 0, 0.55));
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.member-add {
  flex-shrink: 0;
}

.member-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 8px;
}

.member-row.is-coordinator {
  background: rgba(var(--v-theme-amber-darken-2), 0.06);
}

.member-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.member-remove {
  flex-shrink: 0;
}
</style>

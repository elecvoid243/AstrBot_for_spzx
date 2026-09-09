<template>
    <v-dialog v-model="isOpen" max-width="640" @update:model-value="handleDialogChange">
        <v-card>
            <v-card-title class="text-h3 pa-4 pb-0 pl-6">
                {{ isEditing ? tm('project.edit') : tm('project.create') }}
            </v-card-title>
            <v-card-text>
                <v-text-field v-model="form.emoji" :label="tm('project.emoji')" variant="outlined" hide-details class="mb-3" />
                <v-text-field v-model="form.title" :label="tm('project.name')" variant="outlined" hide-details class="mb-3" autofocus
                    @keyup.enter="handleSave" />
                <v-textarea v-model="form.description" :label="tm('project.description')" variant="outlined" hide-details rows="3" />
                <v-divider class="my-4" />
                <v-select v-model="form.workspace_type" :items="workspaceTypeItems" item-title="label" item-value="value"
                    :label="tm('project.workspace.type')" variant="outlined" hide-details class="mb-3" />
                <v-text-field
                    v-if="form.workspace_type === 'custom'"
                    v-model="form.workspace_path"
                    :label="tm('project.workspace.path')"
                    variant="outlined"
                    hide-details
                    class="mb-1"
                    persistent-hint
                    :hint="tm('project.spcode.pathHint')"
                >
                    <template #append-inner>
                        <v-btn
                            icon="mdi-folder-search-outline"
                            variant="text"
                            size="small"
                            data-testid="browse-directory"
                            :title="tm('spcodeProjectLoad.dialog.browse')"
                            :aria-label="tm('spcodeProjectLoad.dialog.browse')"
                            @click="browserOpen = true"
                        />
                    </template>
                </v-text-field>
                <ProjectDirectoryBrowser
                    v-model="browserOpen"
                    @select="onBrowserSelect"
                />
                <!--
                    Recent custom paths, shared with the ChatInput
                    project-load dialog through useProjectPathHistory
                    (same localStorage key), so a path picked in either
                    dialog shows up as recent in the other.
                -->
                <div
                    v-if="form.workspace_type === 'custom' && recentPaths.length"
                    class="mt-3"
                >
                    <div class="text-caption text-medium-emphasis mb-1">
                        {{ tm('spcodeProjectLoad.dialog.historyLabel') }}
                    </div>
                    <v-list density="compact" class="history-list pa-0">
                        <v-list-item
                            v-for="item in recentPaths"
                            :key="item"
                            class="history-item"
                            rounded="md"
                            @click="form.workspace_path = item"
                        >
                            <template #prepend>
                                <v-icon icon="mdi-history" size="x-small" />
                            </template>
                            <v-list-item-title class="text-body-2">
                                {{ item }}
                            </v-list-item-title>
                            <template #append>
                                <!-- @click.stop keeps the row's own click (fill the path) from firing. -->
                                <v-btn
                                    icon="mdi-close"
                                    variant="text"
                                    size="x-small"
                                    density="compact"
                                    :aria-label="tm('spcodeProjectLoad.dialog.removeFromHistory')"
                                    @click.stop="removeFromPathHistory(item)"
                                />
                            </template>
                        </v-list-item>
                    </v-list>
                </div>
                <v-divider v-if="form.workspace_type === 'custom'" class="my-4" />
                <div v-if="form.workspace_type === 'custom'" class="spcode-section">
                    <div class="spcode-section-title">{{ tm('project.spcode.sectionTitle') }}</div>
                    <!--
                        Same pill chips as the ChatInput project-load
                        dialog. The switches for "silent load on session
                        open" and "AGENTS.md only" were removed
                        (2026-09-09): silent loading is now always on and
                        its parameters follow these two chips.
                    -->
                    <div class="spcode-chips">
                        <SpToggleChip
                            v-model="loadAgentsMd"
                            icon="mdi-file-document-outline"
                            :label="tm('spcodeProjectLoad.dialog.loadAgentsMd')"
                        />
                        <SpToggleChip
                            v-model="loadCodegraph"
                            icon="mdi-graph-outline"
                            :label="tm('spcodeProjectLoad.dialog.loadCodegraph')"
                        />
                        <span class="spcode-chips__hint">
                            {{ tm('spcodeProjectLoad.dialog.loadAutoCreateHint') }}
                        </span>
                    </div>
                </div>
                <v-alert
                    v-if="props.errorMessage"
                    class="mt-3"
                    type="error"
                    variant="tonal"
                    density="compact"
                >
                    {{ props.errorMessage }}
                </v-alert>
            </v-card-text>
            <v-card-actions>
                <v-spacer></v-spacer>
                <v-btn variant="text" @click="handleCancel" color="grey-darken-1" :disabled="props.saving">{{ t('core.common.cancel') }}</v-btn>
                <v-btn variant="text" @click="handleSave" color="primary" :disabled="!canSave || props.saving" :loading="props.saving">{{ t('core.common.save') }}</v-btn>
            </v-card-actions>
        </v-card>
    </v-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n, useModuleI18n } from '@/i18n/composables';
import { useProjectPathHistory } from '@/composables/useProjectPathHistory';
import ProjectDirectoryBrowser from './ProjectDirectoryBrowser.vue';
import SpToggleChip from './SpToggleChip.vue';

export type WorkspaceType = 'session' | 'project' | 'custom';

export interface Project {
    project_id: string;
    title: string;
    emoji?: string;
    description?: string;
    workspace_type?: WorkspaceType;
    workspace_path?: string | null;
    resolved_workspace_path?: string | null;
    spcode_no_agentsmd?: boolean;
    spcode_no_codegraph?: boolean;
    created_at: string;
    updated_at: string;
}

export interface ProjectFormData {
    emoji: string;
    title: string;
    description: string;
    workspace_type: WorkspaceType;
    workspace_path: string;
    /**
     * Inverse flags, matching the API/DB naming: true = skip that load
     * step. The chips in the template speak in positive terms via the
     * loadAgentsMd / loadCodegraph writable computeds below.
     *
     * Silent loading on session open is always on and no longer has a
     * user-visible switch (2026-09-09), so there is no spcode_auto_load
     * field here any more.
     */
    spcode_no_agentsmd: boolean;
    spcode_no_codegraph: boolean;
}

interface Props {
    modelValue: boolean;
    project?: Project | null;
    errorMessage?: string;
    saving?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
    modelValue: false,
    project: null,
    errorMessage: '',
    saving: false
});

const emit = defineEmits<{
    'update:modelValue': [value: boolean];
    save: [formData: ProjectFormData, projectId?: string];
}>();

const { t } = useI18n();
const { tm } = useModuleI18n('features/chat');

const isOpen = ref(props.modelValue);
const isEditing = ref(false);
// In-app directory picker (same one ProjectLoadDialog uses); opened by
// the browse button inside the workspace-path field.
const browserOpen = ref(false);
const form = ref<ProjectFormData>({
    emoji: '📁',
    title: '',
    description: '',
    workspace_type: 'project',
    workspace_path: '',
    spcode_no_agentsmd: false,
    spcode_no_codegraph: false,
});

// Shared recent-path history (localStorage singleton) — the same store
// the ChatInput project-load dialog uses.
const { recentPaths, addToPathHistory, removeFromPathHistory } =
    useProjectPathHistory();

// Positive-facing views of the two inverse flags so the template can
// bind the chips with plain v-model.
const loadAgentsMd = computed({
    get: () => !form.value.spcode_no_agentsmd,
    set: (value: boolean) => {
        form.value.spcode_no_agentsmd = !value;
    },
});
const loadCodegraph = computed({
    get: () => !form.value.spcode_no_codegraph,
    set: (value: boolean) => {
        form.value.spcode_no_codegraph = !value;
    },
});
const workspaceTypeItems = computed(() => [
    { label: tm('project.workspace.project'), value: 'project' },
    { label: tm('project.workspace.session'), value: 'session' },
    { label: tm('project.workspace.custom'), value: 'custom' }
]);
const canSave = computed(() => {
    if (!form.value.title.trim()) return false;
    // Only the custom workspace mode takes a user-supplied path; session
    // and project workspaces are auto-allocated by AstrBot.
    if (form.value.workspace_type === 'custom') {
        return form.value.workspace_path.trim().length > 0;
    }
    return true;
});

watch(() => props.modelValue, (newVal) => {
    isOpen.value = newVal;
    if (newVal) {
        if (props.project) {
            isEditing.value = true;
            form.value = {
                emoji: props.project.emoji || '📁',
                title: props.project.title,
                description: props.project.description || '',
                workspace_type: props.project.workspace_type || 'session',
                workspace_path: props.project.workspace_path || '',
                spcode_no_agentsmd: props.project.spcode_no_agentsmd === true,
                spcode_no_codegraph: props.project.spcode_no_codegraph === true,
            };
        } else {
            isEditing.value = false;
            form.value = {
                emoji: '📁',
                title: '',
                description: '',
                workspace_type: 'project',
                workspace_path: '',
                spcode_no_agentsmd: false,
                spcode_no_codegraph: false,
            };
        }
    }
});

watch(() => form.value.workspace_type, (workspaceType) => {
    // Non-custom workspaces are auto-allocated; clear any path entered
    // for the custom mode so it is never submitted.
    if (workspaceType !== 'custom') {
        form.value.workspace_path = '';
    }
});

function handleDialogChange(value: boolean) {
    emit('update:modelValue', value);
}

/**
 * Handle a directory chosen in the in-app file browser: fill the
 * workspace-path field. Same flow as ProjectLoadDialog — the selected
 * absolute path comes straight from the backend, and manual typing
 * remains fully available.
 */
function onBrowserSelect(selectedPath: string) {
    form.value.workspace_path = selectedPath;
}

function handleCancel() {
    isOpen.value = false;
    emit('update:modelValue', false);
}

function handleSave() {
    if (!canSave.value) {
        return;
    }

    // Only the custom mode carries a path; session/project workspaces
    // are auto-allocated by the backend, so submit an empty path.
    const isCustom = form.value.workspace_type === 'custom';
    const workspacePath = isCustom ? form.value.workspace_path.trim() : '';
    if (workspacePath) {
        addToPathHistory(workspacePath);
    }

    emit('save', {
        ...form.value,
        workspace_path: workspacePath,
        // The spcode load steps only apply to custom workspaces; clear
        // stale selections when the project no longer has a path.
        spcode_no_agentsmd: isCustom ? form.value.spcode_no_agentsmd : false,
        spcode_no_codegraph: isCustom ? form.value.spcode_no_codegraph : false,
    }, props.project?.project_id);
}

</script>

<style scoped>
.dialog-title {
    font-size: 22px;
    font-weight: 500;
}

.spcode-section {
    margin-top: 4px;
}

.spcode-section-title {
    font-size: 13px;
    font-weight: 600;
    color: rgba(var(--v-theme-on-surface), 0.78);
    margin-bottom: 8px;
}

/*
 * Load-step chips row (SpToggleChip). Unlike the ChatInput dialog this
 * row sits under outlined text fields rather than radios, so it needs no
 * inline-start inset — it lines up with the section title above it.
 */
.spcode-chips {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
}

.spcode-chips__hint {
    margin-inline-start: auto;
    font-size: 12px;
    color: rgba(var(--v-theme-on-surface), 0.56);
}

/* Recent-path list — mirrors the ChatInput project-load dialog. */
.history-list {
    max-height: 160px;
    overflow-y: auto;
    background: transparent;
}

.history-item :deep(.v-list-item-title) {
    font-family: "Fira Code", "Consolas", monospace;
    font-size: 12px;
    word-break: break-all;
    white-space: normal;
}
</style>

<template>
    <!--
      2026-10-02 (elecvoid243): "chip" variant — always-visible pill in the
      composer. The label IS the live current config profile (shared
      useChatConfigSelection singleton), and switching takes one click.
    -->
    <div v-if="variant === 'chip'" class="config-chip-wrap">
        <v-menu v-model="menuOpen" location="top" offset="8" transition="none">
            <template #activator="{ props: menuProps }">
                <button
                    v-bind="menuProps"
                    type="button"
                    class="config-chip"
                    :class="{ 'config-chip--open': menuOpen }"
                    :disabled="loadingConfigs || saving"
                    :title="tm('config.title')"
                >
                    <span class="config-chip-dot" aria-hidden="true"></span>
                    <span class="config-chip-label">{{ selectedConfigLabel }}</span>
                    <v-progress-circular
                        v-if="saving"
                        size="13"
                        width="2"
                        indeterminate
                        class="config-chip-spinner"
                    />
                    <v-icon v-else size="14" class="config-chip-chevron">mdi-chevron-down</v-icon>
                </button>
            </template>
            <v-card class="config-menu-card" elevation="0">
                <div class="config-menu-title">{{ tm('config.title') }}</div>
                <v-list density="compact" nav class="config-menu-list">
                    <v-list-item
                        v-for="config in configOptions"
                        :key="config.id"
                        :active="selectedConfigId === config.id"
                        rounded="lg"
                        class="config-menu-item"
                        @click="handleChipSelect(config.id)"
                    >
                        <v-list-item-title class="config-item-title">{{ config.name }}</v-list-item-title>
                        <v-list-item-subtitle v-if="config.name !== config.id" class="config-item-subtitle">
                            {{ config.id }}
                        </v-list-item-subtitle>
                        <template #append>
                            <v-icon v-if="selectedConfigId === config.id" size="18" color="primary">mdi-check</v-icon>
                        </template>
                    </v-list-item>
                </v-list>
                <div
                    v-if="!loadingConfigs && configOptions.length === 0"
                    class="config-menu-empty"
                >
                    暂无可选配置，请先在配置页创建。
                </div>
            </v-card>
        </v-menu>
    </div>

    <!-- "menu" variant: the original "+" menu list item + dialog. -->
    <div v-else>
        <v-list-item
            class="styled-menu-item"
            rounded="md"
            @click="openDialog"
            :disabled="loadingConfigs || saving"
        >
            <template v-slot:prepend>
                <v-icon icon="mdi-cog-outline" size="small"></v-icon>
            </template>
            <v-list-item-title>
                {{ tm('config.title') }}
            </v-list-item-title>
            <v-list-item-subtitle class="text-caption">
                {{ selectedConfigLabel }}
            </v-list-item-subtitle>
            <template v-slot:append>
                <v-icon icon="mdi-chevron-right" size="small" class="text-medium-emphasis"></v-icon>
            </template>
        </v-list-item>

        <v-dialog v-model="dialog" max-width="480">
            <v-card>
                <v-card-title class="text-h3 pa-4 pb-0 pl-6 d-flex align-center justify-space-between">
                    <span>选择配置文件</span>
                    <v-btn icon variant="text" @click="closeDialog">
                        <v-icon>mdi-close</v-icon>
                    </v-btn>
                </v-card-title>
                <v-card-text>
                    <div v-if="loadingConfigs" class="text-center py-6">
                        <v-progress-circular indeterminate color="primary"></v-progress-circular>
                    </div>

                    <v-list v-else class="config-list" density="comfortable">
                        <v-list-item
                            v-for="config in configOptions"
                            :key="config.id"
                            :active="tempSelectedConfig === config.id"
                            rounded="lg"
                            variant="text"
                            @click="tempSelectedConfig = config.id"
                        >
                            <v-list-item-title>{{ config.name }}</v-list-item-title>
                            <v-list-item-subtitle class="text-caption text-grey">
                                {{ config.id }}
                            </v-list-item-subtitle>
                            <template #append>
                                <v-icon v-if="tempSelectedConfig === config.id" color="primary">mdi-check</v-icon>
                            </template>
                        </v-list-item>
                        <div v-if="configOptions.length === 0" class="text-center text-body-2 text-medium-emphasis">
                            暂无可选配置，请先在配置页创建。
                        </div>
                    </v-list>
                </v-card-text>
                <v-card-actions>
                    <v-spacer></v-spacer>
                    <v-btn variant="text" @click="closeDialog">取消</v-btn>
                    <v-btn
                        color="primary"
                        variant="tonal"
                        @click="confirmSelection"
                        :disabled="!tempSelectedConfig"
                        :loading="saving"
                    >
                        应用
                    </v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>
    </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { useModuleI18n } from '@/i18n/composables';
import { useChatConfigSelection } from '@/composables/useChatConfigSelection';

withDefaults(defineProps<{
    variant?: 'menu' | 'chip';
}>(), {
    variant: 'menu'
});

const { tm } = useModuleI18n('features/chat');

const {
    configOptions,
    loadingConfigs,
    selectedConfigId,
    selectedConfigLabel,
    saving,
    selectConfig
} = useChatConfigSelection();

const dialog = ref(false);
const tempSelectedConfig = ref('');
const menuOpen = ref(false);

function openDialog() {
    tempSelectedConfig.value = selectedConfigId.value;
    dialog.value = true;
}

function closeDialog() {
    dialog.value = false;
}

async function confirmSelection() {
    if (!tempSelectedConfig.value) {
        return;
    }
    await selectConfig(tempSelectedConfig.value);
    dialog.value = false;
}

async function handleChipSelect(confId: string) {
    if (confId === selectedConfigId.value) {
        return;
    }
    await selectConfig(confId);
}
</script>

<style scoped>
.config-list {
    max-height: 360px;
    overflow-y: auto;
}

/* Chip: ghost pill (2026-10-03, elecvoid243) — no border/background at
   rest; hover reveals a wash. The status dot carries the "this is the
   live config" semantics. */
.config-chip-wrap {
    display: inline-flex;
    min-width: 0;
}

.config-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 30px;
    max-width: min(180px, 30vw);
    padding: 0 9px;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: rgba(var(--v-theme-on-surface), 0.72);
    cursor: pointer;
    font: inherit;
    letter-spacing: 0;
    transition: background-color 150ms ease, color 150ms ease;
}

.config-chip:hover {
    background: var(--sp-ghost-hover-bg, rgba(var(--v-theme-on-surface), 0.055));
    color: rgb(var(--v-theme-on-surface));
}

.config-chip--open {
    background: var(--sp-ghost-open-bg, rgba(var(--v-theme-on-surface), 0.07));
    color: rgb(var(--v-theme-on-surface));
}

.config-chip:disabled {
    opacity: 0.6;
    cursor: default;
}

.config-chip:focus-visible {
    outline: 2px solid rgb(var(--v-theme-primary));
    outline-offset: 1px;
}

.config-chip-dot {
    flex: 0 0 6px;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: rgb(var(--v-theme-success));
}

.config-chip-chevron {
    flex: 0 0 auto;
    opacity: 0.5;
}

.config-chip-label {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 13px;
    font-weight: 500;
}

.config-chip-spinner {
    flex: 0 0 auto;
}

.config-menu-card {
    width: min(300px, calc(100vw - 24px));
    overflow: hidden;
    border: 1px solid rgba(var(--v-theme-on-surface), 0.09);
    border-radius: 12px;
}

.config-menu-title {
    padding: 10px 14px 2px;
    font-size: 12px;
    font-weight: 600;
    color: rgba(var(--v-theme-on-surface), 0.55);
}

.config-menu-list {
    max-height: 320px;
    overflow-y: auto;
    padding: 4px 8px 8px;
}

.config-item-title {
    font-size: 14px;
}

.config-item-subtitle {
    font-size: 12px;
}

.config-menu-empty {
    padding: 16px;
    text-align: center;
    font-size: 13px;
    color: rgba(var(--v-theme-on-surface), 0.55);
}

@media (max-width: 768px) {
    .config-chip {
        max-width: 34vw;
    }
}
</style>

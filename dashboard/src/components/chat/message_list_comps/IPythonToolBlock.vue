<template>
    <div class="ipython-tool-block" :class="{ compact: !showHeader }">
        <div v-if="displayExpanded" class="py-3 animate-fade-in">
            <!-- Code Section (matches ToolResultView result-code style) -->
            <div class="code-section">
                <CopyableText
                    :value="code"
                    mode="block"
                    :multiline="true"
                    bare
                    :show-icon="!!code"
                >
                    <div
                        v-if="shikiReady && code"
                        class="code-highlighted code-result-shiki"
                        v-html="highlightedCode"
                    ></div>
                    <pre v-else class="code-fallback">{{ code || 'No code available' }}</pre>
                </CopyableText>
            </div>

            <!-- Result Section (matches ToolResultView result-code style) -->
            <div v-if="result || resultLoading || resultError" class="result-section">
                <div class="result-label">
                    {{ tm('ipython.output') }}:
                </div>
                <div v-if="resultLoading" class="result-status">Loading…</div>
                <div v-else-if="resultError" class="result-status result-status--error">
                    {{ resultError }}
                </div>
                <template v-else>
                    <CopyableText
                        :value="formattedResult"
                        mode="block"
                        :multiline="true"
                        bare
                    >
                        <pre class="result-content">{{ formattedResult }}</pre>
                    </CopyableText>
                    <CopyableText
                        v-if="resultNotice"
                        :value="resultNotice"
                        mode="block"
                        :multiline="true"
                        bare
                        class="result-suffix"
                    />
                </template>
            </div>
        </div>
    </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useModuleI18n } from '@/i18n/composables';
import { fetchWithAuth } from '@/api/http';
import { chatApi } from '@/api/v1';
import { ensureShikiLanguages, escapeHtml, renderShikiCode } from '@/utils/shiki';
import { findSystemNoticeIndex } from '@/utils/systemNotice';
import CopyableText from "./__shared__/CopyableText.vue";

const props = defineProps({
    toolCall: {
        type: Object,
        required: true
    },
    isDark: {
        type: Boolean,
        default: false
    },
    initialExpanded: {
        type: Boolean,
        default: false
    },
    showHeader: {
        type: Boolean,
        default: true
    },
    forceExpanded: {
        type: Boolean,
        default: null
    },
    messageId: {
        type: [String, Number],
        default: null
    }
});

const { tm } = useModuleI18n('features/chat');
const isExpanded = ref(props.initialExpanded);
const shikiHighlighter = ref(null);
const shikiReady = ref(false);
const resolvedResult = ref('');
const resultLoading = ref(false);
const resultError = ref('');

const code = computed(() => {
    try {
        if (props.toolCall.args && props.toolCall.args.code) {
            return props.toolCall.args.code;
        }
    } catch (err) {
        console.error('Failed to get iPython code:', err);
    }
    return null;
});

// Ticket 05: a >64KB result arrives as an out-of-band reference, so fetch the
// full text once the block is visible instead of rendering an empty output.
const resultRef = computed(() => {
    const ref = props.toolCall.result_ref;
    return ref && typeof ref === 'object' ? ref : null;
});

const result = computed(() => resolvedResult.value || props.toolCall.result);

const formattedResult = computed(() => {
    if (!result.value) return '';
    let text = result.value;
    const idx = findSystemNoticeIndex(text);
    if (idx >= 0) {
        text = text.slice(0, idx).trim();
    }
    try {
        const parsed = JSON.parse(text);
        return JSON.stringify(parsed, null, 2);
    } catch {
        return text;
    }
});

const resultNotice = computed(() => {
    if (!result.value) return null;
    const idx = findSystemNoticeIndex(result.value);
    if (idx < 0) return null;
    return result.value.slice(idx).trim();
});

const highlightedCode = computed(() => {
    if (!shikiReady.value || !shikiHighlighter.value || !code.value) {
        return '';
    }
    try {
        return renderShikiCode(
            shikiHighlighter.value,
            code.value,
            'python',
            props.isDark ? 'dark' : 'light'
        );
    } catch (err) {
        console.error('Failed to highlight code:', err);
        return `<pre><code>${escapeHtml(code.value)}</code></pre>`;
    }
});

const displayExpanded = computed(() => {
    if (props.forceExpanded === null) {
        return isExpanded.value;
    }
    return props.forceExpanded;
});

const loadFullResult = async () => {
    const ref = resultRef.value;
    if (!ref?.run_id || !ref?.tool_call_id) return;
    if (resolvedResult.value || resultLoading.value) return;
    resultLoading.value = true;
    resultError.value = '';
    try {
        let response = await fetchWithAuth(
            chatApi.runToolResultUrl(String(ref.run_id), String(ref.tool_call_id))
        );
        let body = await response.json().catch(() => null);
        // The run may be gone (history reload); fall back to the persisted message.
        if ((!response.ok || body?.status === 'error') && props.messageId != null) {
            response = await fetchWithAuth(
                chatApi.messageToolResultUrl(props.messageId, String(ref.tool_call_id))
            );
            body = await response.json().catch(() => null);
        }
        if (!response.ok || body?.status === 'error') {
            throw new Error(body?.message || `Load failed (${response.status})`);
        }
        const text = body?.data?.result;
        resolvedResult.value =
            typeof text === 'string' ? text : JSON.stringify(text ?? '');
    } catch (err) {
        resultError.value = String(err?.message || err);
    } finally {
        resultLoading.value = false;
    }
};

watch(
    displayExpanded,
    (expanded) => {
        if (expanded) void loadFullResult();
    },
    { immediate: true }
);

onMounted(async () => {
    try {
        shikiHighlighter.value = await ensureShikiLanguages(['python']);
        shikiReady.value = true;
    } catch (err) {
        console.error('Failed to initialize Shiki:', err);
    }
});
</script>

<style scoped>
.ipython-tool-block {
    margin-bottom: 12px;
    margin-top: 6px;
    font-size: inherit;
    line-height: inherit;
}

.ipython-tool-block.compact {
    margin: 0;
}

.py-3 {
    padding-top: 12px;
    padding-bottom: 12px;
}

/* ── Code section (matches ToolResultView .result-code) ──────── */

.code-section {
    margin-bottom: 8px;
}

/* Fallback (non-Shiki) code block */
.code-fallback {
    margin: 0;
    padding: 8px 10px;
    border-radius: 4px;
    overflow-x: auto;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 11.5px;
    line-height: 1.55;
    background: rgba(var(--v-theme-on-surface), 0.04);
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 300px;
    overflow-y: auto;
}

/* Shiki highlighted code — mirrors ToolResultView .result-code-shiki */
.code-result-shiki {
    padding: 0;
    border-radius: 4px;
    overflow: hidden;
}

:deep(.code-result-shiki pre.shiki) {
    margin: 0;
    padding: 8px 10px;
    border-radius: 4px;
    overflow: auto;
    max-height: 300px;
    font-size: 11.5px;
    line-height: 1.55;
    tab-size: 4;
}

:deep(.code-result-shiki pre.shiki code) {
    display: block;
    padding: 0;
    background: transparent;
    font-family: inherit;
    font-size: inherit;
    line-height: inherit;
}

/* ── Result section ─────────────────────────────────────────── */

.result-section {
    margin-top: 8px;
}

.result-label {
    font-size: 11px;
    font-weight: 600;
    color: rgba(var(--v-theme-on-surface), 0.55);
    margin-bottom: 4px;
    opacity: 0.8;
}

.result-status {
    font-size: 12px;
    color: rgba(var(--v-theme-on-surface), 0.55);
}

.result-status--error {
    color: rgb(var(--v-theme-error));
}

.result-content {
    margin: 0;
    padding: 8px 10px;
    border-radius: 4px;
    overflow-x: auto;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 11.5px;
    line-height: 1.55;
    background: rgba(var(--v-theme-on-surface), 0.04);
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 300px;
    overflow-y: auto;
}

/* ── System notice suffix ───────────────────────────────────── */
.result-suffix {
    margin-top: 6px;
    padding: 4px 8px;
    border-radius: 4px;
    background: rgba(var(--v-theme-on-surface), 0.03);
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 11px;
    line-height: 1.55;
    color: rgba(var(--v-theme-on-surface), 0.55);
    white-space: pre-wrap;
    word-break: break-word;
}

.animate-fade-in {
    animation: fadeIn 0.2s ease-in-out;
}

@keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
}
</style>

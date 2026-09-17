<template>
  <div v-if="modelValue" class="search-overlay" @click.self="closeDialog">
    <div class="search-dialog">
      <div class="search-header">
        <div class="search-input-wrap">
          <Search :size="16" class="search-icon" />
          <input
            ref="inputRef"
            v-model="query"
            type="text"
            class="search-input"
            :placeholder="tm('search.placeholder')"
            @keydown.enter="onSearch"
            @keydown.escape="closeDialog"
          />
          <button v-if="query" class="clear-btn" @click="clearQuery">
            <X :size="14" />
          </button>
          <span v-else class="enter-hint">Enter</span>
        </div>
        <button class="search-btn" :disabled="!query.trim() || loading" @click="onSearch">
          {{ tm('search.action') }}
        </button>
        <button class="esc-btn" @click="closeDialog">
          <span class="esc-hint">ESC</span>
        </button>
      </div>

      <div v-if="loading" class="search-loading">
        <v-progress-circular indeterminate size="18" width="2" />
        <span class="loading-text">{{ tm('search.loading') }}</span>
      </div>

      <div v-else-if="error" class="search-error">
        <span class="error-text">{{ error }}</span>
        <button class="retry-btn" @click="onSearch">{{ tm('search.retry') }}</button>
      </div>

      <div v-else-if="hasSearched && results.length === 0" class="search-empty">
        <span class="empty-text">{{ tm('search.noResults') }}</span>
      </div>

      <div v-else-if="!hasSearched" class="search-empty">
        <span class="empty-text">{{ tm('search.hint') }}</span>
      </div>

      <div v-else-if="results.length > 0" class="search-results">
        <div v-for="conv in results" :key="conv.session_id" class="result-group">
          <div class="group-head" @click="toggleGroup(conv.session_id)">
            <span class="group-title">{{ conv.title }}</span>
            <span class="match-count">{{ conv.matches.length }} {{ tm('search.matches') }}</span>
            <ChevronDown :size="14" :class="{ expanded: expanded[conv.session_id] }" />
          </div>
          <div v-if="expanded[conv.session_id]" class="match-list">
            <div
              v-for="(m, i) in conv.matches"
              :key="i"
              class="match-item"
              @click="goTo(conv, m)"
            >
              <span v-html="highlight(m.snippet, query)" />
            </div>
          </div>
        </div>

        <div v-if="pag.total_pages > 1" class="search-pager">
          <button :disabled="pag.page <= 1" @click="goPage(pag.page - 1)">
            {{ tm('search.prevPage') }}
          </button>
          <span class="pager-info">{{ pag.page }}/{{ pag.total_pages }}</span>
          <button :disabled="pag.page >= pag.total_pages" @click="goPage(pag.page + 1)">
            {{ tm('search.nextPage') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue';
import { useRouter } from 'vue-router';
import { Search, X, ChevronDown } from "@lucide/vue";
import { messageApi, type SearchConversation, type SearchMatch } from '@/api/messages';
import { useModuleI18n } from '@/i18n/composables';

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>();

const { tm } = useModuleI18n('features/chat');
const router = useRouter();

const inputRef = ref<HTMLInputElement | null>(null);
const query = ref('');
const loading = ref(false);
const error = ref('');
const hasSearched = ref(false);
const results = ref<SearchConversation[]>([]);
const expanded = ref<Record<string, boolean>>({});
const pag = ref({ page: 1, page_size: 20, total: 0, total_pages: 1 });

let lastQ = '';

watch(() => props.modelValue, (v) => {
  if (v) nextTick(() => inputRef.value?.focus());
  else reset();
});

function closeDialog() { emit('update:modelValue', false); }

function reset() {
  query.value = '';
  lastQ = '';
  results.value = [];
  hasSearched.value = false;
  loading.value = false;
  error.value = '';
  pag.value = { page: 1, page_size: 20, total: 0, total_pages: 1 };
  expanded.value = {};
}

function clearQuery() {
  query.value = '';
  results.value = [];
  hasSearched.value = false;
  inputRef.value?.focus();
}

async function onSearch() {
  const q = query.value.trim();
  if (!q || loading.value) return;
  loading.value = true;
  error.value = '';
  hasSearched.value = true;
  try {
    const data = await messageApi.searchMessages(q, 1, 20);
    results.value = data.results || [];
    pag.value = data.pagination || pag.value;
    if (results.value.length > 0) expanded.value[results.value[0].session_id] = true;
    lastQ = q;
  } catch (e: any) {
    error.value = e?.message || tm('search.error');
    results.value = [];
  } finally {
    loading.value = false;
  }
}

function toggleGroup(sid: string) { expanded.value[sid] = !expanded.value[sid]; }

function goPage(p: number) {
  if (!lastQ) return;
  messageApi.searchMessages(lastQ, p, 20).then((d) => {
    results.value = d.results || [];
    pag.value = d.pagination || pag.value;
  });
}

function goTo(conv: SearchConversation, m: SearchMatch) {
  closeDialog();
  // The keyword rides along: a hit inside a collapsed agent-work capsule (or
  // inside its thinking text) only becomes readable if the list knows what
  // was searched for.
  router.push({
    name: 'ChatDetail',
    params: { conversationId: conv.session_id },
    query: {
      scrollToIndex: String(m.message_index),
      scrollToKeyword: query.value.trim(),
    },
  });
}

function highlight(snippet: string, kw: string): string {
  if (!kw.trim()) return esc(snippet);
  const re = new RegExp(escRE(esc(kw)), 'gi');
  return esc(snippet).replace(re, '<mark>$&</mark>');
}

function esc(s: string): string {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function escRE(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
</script>

<style scoped>
.search-overlay {
  position: fixed;
  inset: 0;
  background: transparent;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 10vh;
  /* 2026-07-28 (elecvoid243): modal level — must sit above the app toolbar
     (z-index 1200) and the todo summary bar (1400). The previous value 1000
     was too low and let those layers paint over the dialog.
     NOTE: deliberately NOT teleported to <body>. Vuetify theme colors are
     CSS variables (--v-theme-surface / --v-theme-on-surface) scoped to the
     .v-application subtree; teleporting to a bare <body> drops that scope and
     makes the dialog background / text transparent. It is safe to keep the
     overlay inside the Chat subtree because no ancestor (.chat-ui, v-main,
     v-layout, v-application) creates a stacking context or a containing block
     (no transform / filter / contain / will-change), so position:fixed still
     covers the viewport and z-index 2000 wins in the root stacking context. */
  z-index: 2000;
}
.search-dialog {
  width: 640px;
  max-width: calc(100vw - 48px);
  min-height: 160px;
  max-height: min(70vh, 620px);
  background: rgb(var(--v-theme-surface));
  border-radius: 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.28), 0 4px 14px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  animation: search-pop 0.16s ease-out;
}
.search-header {
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}
.search-input-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-radius: 6px;
}
.search-icon { color: rgba(var(--v-theme-on-surface),0.45); flex-shrink:0; }
.search-input {
  flex:1; border:none; outline:none; background:transparent; font-size:13px; color:rgb(var(--v-theme-on-surface));
}
.search-input::placeholder { color: rgba(var(--v-theme-on-surface),0.4); }
.clear-btn {
  border:none; background:none; cursor:pointer; color:rgba(var(--v-theme-on-surface),0.45); display:flex; align-items:center; padding:0;
}
.clear-btn:hover { color:rgb(var(--v-theme-on-surface)); }
.enter-hint {
  flex-shrink:0; font-size:10.5px; color:rgba(var(--v-theme-on-surface),0.35);
  border:1px solid rgba(var(--v-theme-on-surface),0.12); border-radius:4px; padding:1px 5px;
}
.search-btn {
  flex-shrink:0; border:none; cursor:pointer; font-size:12.5px; font-weight:500;
  padding:6px 16px; border-radius:6px; color:rgb(var(--v-theme-on-surface));
  background:rgba(var(--v-theme-on-surface),0.06);
}
.search-btn:hover:not(:disabled) { background:rgba(var(--v-theme-on-surface),0.1); }
.search-btn:disabled { opacity:0.4; cursor:default; }
.esc-btn { border:none; background:none; cursor:pointer; padding:4px 8px; border-radius:4px; }
.esc-hint { font-size:11px; color:rgba(var(--v-theme-on-surface),0.4); }
.esc-btn:hover { background:rgba(var(--v-theme-on-surface),0.06); }
.search-loading, .search-error, .search-empty {
  padding: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
}
.loading-text, .error-text, .empty-text {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface),0.5);
}
.error-text { color: #e53935; }
.retry-btn {
  border:none; background:none; cursor:pointer; font-size:12px; color:var(--v-primary-base,#1976d2); padding:2px 6px;
}
.search-results { flex:1; overflow-y:auto; padding:8px 0; }
.result-group { border-bottom:1px solid rgba(var(--v-theme-on-surface),0.04); }
.group-head {
  padding:8px 16px; display:flex; align-items:center; gap:8px; cursor:pointer;
}
.group-head:hover { background:rgba(var(--v-theme-on-surface),0.03); }
.group-title {
  flex:1; font-size:12px; font-weight:500; color:rgb(var(--v-theme-on-surface)); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.match-count { font-size:10.5px; color:rgba(var(--v-theme-on-surface),0.4); }
.expanded { transition:transform 0.15s; }
.expanded { transform:rotate(180deg); }
.match-list { padding:0 16px 8px 32px; }
.match-item {
  padding:4px 0; font-size:11.5px; color:rgba(var(--v-theme-on-surface),0.7); line-height:1.5; cursor:pointer;
}
.match-item:hover { color:rgb(var(--v-theme-on-surface)); }
.match-item mark {
  background:rgba(255,235,59,0.35); color:inherit; border-radius:2px; padding:0 1px;
}
.search-pager {
  padding:10px 16px; display:flex; align-items:center; justify-content:center; gap:12px; border-top:1px solid rgba(var(--v-theme-on-surface),0.06);
}
.search-pager button {
  border:none; background:none; cursor:pointer; font-size:11px; color:rgba(var(--v-theme-on-surface),0.6); padding:2px 8px; border-radius:3px;
}
.search-pager button:hover:not(:disabled) { background:rgba(var(--v-theme-on-surface),0.05); color:rgb(var(--v-theme-on-surface)); }
.search-pager button:disabled { opacity:0.35; cursor:default; }
.pager-info { font-size:10.5px; color:rgba(var(--v-theme-on-surface),0.4); }

/* 2026-07-28 (elecvoid243): micro-interactions for the floating panel. */
.search-input-wrap {
  transition: background 0.15s ease, box-shadow 0.15s ease;
}
.search-input-wrap:focus-within {
  background: rgba(var(--v-theme-on-surface), 0.07);
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-primary), 0.55);
}
.group-head, .match-item, .esc-btn, .clear-btn, .search-btn {
  transition: background 0.12s ease, color 0.12s ease;
}
@keyframes search-pop {
  from { opacity: 0; transform: translateY(-6px) scale(0.985); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
</style>

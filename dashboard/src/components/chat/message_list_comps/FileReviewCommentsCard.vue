<!-- Author: elecvoid243, 2026-07-30
     Renders a parsed "[File review comments]" block (see
     utils/parseFileReviewComments) as a collapsible review card.

     Styling uses AstrBot's Vuetify theme tokens (rgb(var(--v-theme-*)))
     exclusively, so it adapts to PurpleTheme / PurpleThemeDark with no
     extra theming work: primary blue for the annotation accent, success /
     error for diff add / del, and the shared preBg / surface for the code
     region. Historical messages get this rendering automatically because
     the block is parsed back out of the stored message text. -->
<template>
  <div class="frc">
    <div class="frc-head">
      <v-icon size="15" color="primary">mdi-comment-text-outline</v-icon>
      <span class="frc-title">{{
        tm("spcodeProjectLoad.fileReviewCard.title")
      }}</span>
      <span class="frc-chip">
        {{
          tm("spcodeProjectLoad.fileReviewCard.commentsCount", {
            count: review.totalComments,
          })
        }}
      </span>
      <span class="frc-chip frc-chip--dim">
        {{
          tm("spcodeProjectLoad.fileReviewCard.filesCount", {
            count: review.files.length,
          })
        }}
      </span>
      <span class="frc-spacer" />
      <span class="frc-note">{{
        tm("spcodeProjectLoad.fileReviewCard.note")
      }}</span>
    </div>

    <section
      v-for="(file, idx) in review.files"
      :key="idx"
      class="frc-file"
      :class="{ 'frc-file--closed': isClosed(idx) }"
    >
      <button
        class="frc-fhead"
        type="button"
        :aria-expanded="!isClosed(idx)"
        :aria-label="
          tm(
            isClosed(idx)
              ? 'spcodeProjectLoad.fileReviewCard.expandAria'
              : 'spcodeProjectLoad.fileReviewCard.collapseAria',
            { path: file.path },
          )
        "
        @click="toggle(idx)"
      >
        <span class="frc-dot" />
        <span class="frc-path">{{ file.path }}</span>
        <span class="frc-meta">{{ metaLabel(file) }}</span>
        <span class="frc-ccount">
          {{
            tm("spcodeProjectLoad.fileReviewCard.commentsCount", {
              count: file.comments.length,
            })
          }}
        </span>
        <v-icon size="13" class="frc-chev">mdi-chevron-down</v-icon>
      </button>

      <div class="frc-body">
        <div class="frc-bwrap">
          <div class="frc-code">
            <template v-for="(line, li) in file.lines" :key="li">
              <div v-if="line.hunkHeader" class="frc-hunkhdr">
                {{ line.hunkHeader }}
              </div>
              <div v-else class="frc-line" :class="lineClass(line)">
                <span class="frc-ln">{{ line.no }}</span>
                <span class="frc-sign">{{ sign(line) }}</span>
                <code class="frc-lc">{{ line.text }}</code>
              </div>
              <div v-if="line.comment" class="frc-callout">
                <span class="frc-ctag">
                  {{ tm("spcodeProjectLoad.fileReviewCard.commentTag") }}
                </span>
                <div class="frc-ctext">{{ line.comment }}</div>
              </div>
            </template>
          </div>

          <div
            v-if="file.kind === 'hunk' && file.comments.length"
            class="frc-hcs"
          >
            <div v-for="(c, ci) in file.comments" :key="ci" class="frc-hc">
              <span class="frc-hcln">
                {{
                  tm("spcodeProjectLoad.fileReviewCard.lineBadge", {
                    line: c.line,
                  })
                }}
              </span>
              <div class="frc-hctext">{{ c.text }}</div>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import type {
  FileReviewBlock,
  ReviewFile,
  ReviewLine,
} from "@/utils/parseFileReviewComments";

defineProps<{ review: FileReviewBlock }>();

const { tm } = useModuleI18n("features/chat");

// Per-file collapse state (all expanded by default).
const closed = ref<Record<number, boolean>>({});
function isClosed(idx: number): boolean {
  return !!closed.value[idx];
}
function toggle(idx: number): void {
  closed.value[idx] = !closed.value[idx];
}

function metaLabel(file: ReviewFile): string {
  if (file.kind === "hunk") return file.hunkHeader ?? file.meta;
  if (file.meta.includes("-")) {
    const [start, end] = file.meta.split("-");
    return tm("spcodeProjectLoad.fileReviewCard.lineRangeMeta", { start, end });
  }
  return tm("spcodeProjectLoad.fileReviewCard.lineMeta", { line: file.meta });
}

function lineClass(line: ReviewLine) {
  return {
    "frc-line--anchor": line.anchor,
    "frc-line--add": line.type === "add",
    "frc-line--del": line.type === "del",
  };
}

function sign(line: ReviewLine): string {
  if (line.type === "add") return "+";
  if (line.type === "del") return "-";
  return " ";
}
</script>

<style scoped>
.frc {
  width: 100%;
  margin-top: 10px;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 14px;
  overflow: hidden;
  text-align: left;
}

.frc-head {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 12px 14px;
  flex-wrap: wrap;
  background: linear-gradient(
    120deg,
    rgba(var(--v-theme-primary), 0.07),
    transparent
  );
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}
.frc-title {
  font-size: 12px;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
}
.frc-chip {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10.5px;
  font-weight: 600;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.12);
  border-radius: 99px;
  padding: 2px 9px;
  white-space: nowrap;
}
.frc-chip--dim {
  color: rgba(var(--v-theme-on-surface), 0.6);
  background: rgba(var(--v-theme-on-surface), 0.07);
}
.frc-spacer {
  flex: 1;
}
.frc-note {
  font-size: 10.5px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  white-space: normal;
}

/* ── file section ── */
.frc-file {
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}
.frc-file:last-child {
  border-bottom: 0;
}
.frc-fhead {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  border: 0;
  background: transparent;
  cursor: pointer;
  padding: 9px 14px;
  text-align: left;
  font-family: inherit;
  transition: background 0.15s;
}
.frc-fhead:hover {
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.frc-dot {
  flex: none;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: rgb(var(--v-theme-primary));
}
.frc-path {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12.5px;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.frc-meta {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10.5px;
  font-weight: 500;
  color: rgba(var(--v-theme-on-surface), 0.55);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 4px;
  padding: 1px 7px;
  white-space: nowrap;
}
.frc-ccount {
  margin-left: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10.5px;
  font-weight: 600;
  color: rgb(var(--v-theme-primary));
  white-space: nowrap;
}
.frc-chev {
  flex: none;
  color: rgba(var(--v-theme-on-surface), 0.4);
  transition: transform 0.25s;
}
.frc-file--closed .frc-chev {
  transform: rotate(-90deg);
}

/* collapse via grid-rows so height animates without measuring */
.frc-body {
  display: grid;
  grid-template-rows: 1fr;
  transition: grid-template-rows 0.3s ease;
}
.frc-file--closed .frc-body {
  grid-template-rows: 0fr;
}
.frc-bwrap {
  overflow: hidden;
  min-height: 0;
}

/* ── code region ── */
.frc-code {
  background: rgb(var(--v-theme-preBg));
  margin: 0 12px 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 8px;
  padding: 6px 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
  overflow-x: auto;
}
.frc-line {
  display: flex;
  align-items: baseline;
  padding: 0 12px;
  border-left: 2px solid transparent;
  transition: background 0.12s;
}
.frc-line:hover {
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.frc-ln {
  flex: none;
  width: 36px;
  text-align: right;
  margin-right: 12px;
  color: rgba(var(--v-theme-on-surface), 0.4);
  user-select: none;
}
.frc-sign {
  flex: none;
  width: 12px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  user-select: none;
}
.frc-lc {
  white-space: pre;
  color: rgba(var(--v-theme-on-surface), 0.88);
}
.frc-line--add {
  background: rgba(var(--v-theme-success), 0.1);
}
.frc-line--add .frc-sign,
.frc-line--add .frc-lc {
  color: rgb(var(--v-theme-success));
}
.frc-line--del {
  background: rgba(var(--v-theme-error), 0.1);
}
.frc-line--del .frc-sign,
.frc-line--del .frc-lc {
  color: rgb(var(--v-theme-error));
}
.frc-line--anchor {
  border-left-color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.08);
}
.frc-line--anchor .frc-ln {
  color: rgb(var(--v-theme-primary));
  font-weight: 700;
}
.frc-hunkhdr {
  padding: 2px 12px 6px;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  user-select: none;
}

/* ── inline comment callout (window sections) ── */
.frc-callout {
  margin: 4px 12px 8px 60px;
  border-left: 2px solid rgba(var(--v-theme-primary), 0.5);
  background: rgba(var(--v-theme-primary), 0.07);
  border-radius: 0 6px 6px 0;
  padding: 8px 12px;
}
.frc-ctag {
  display: inline-block;
  font-size: 9px;
  font-weight: 700;
  color: rgb(var(--v-theme-primary));
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-primary), 0.16);
  border-radius: 4px;
  padding: 1px 6px;
  margin-bottom: 5px;
}
.frc-ctext {
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  color: rgba(var(--v-theme-on-surface), 0.88);
}

/* ── hunk comment list ── */
.frc-hcs {
  margin: 0 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.frc-hc {
  display: flex;
  gap: 10px;
  align-items: baseline;
  background: rgba(var(--v-theme-primary), 0.07);
  border: 1px solid rgba(var(--v-theme-primary), 0.3);
  border-radius: 6px;
  padding: 7px 11px;
}
.frc-hcln {
  flex: none;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10.5px;
  font-weight: 700;
  color: rgb(var(--v-theme-primary));
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-primary), 0.16);
  border-radius: 4px;
  padding: 1px 7px;
}
.frc-hctext {
  font-size: 12.5px;
  line-height: 1.55;
  white-space: pre-wrap;
  color: rgba(var(--v-theme-on-surface), 0.88);
}
@media (max-width: 600px) {
  .frc-note,
  .frc-meta {
    display: none;
  }
  .frc-fhead {
    flex-wrap: wrap;
    gap: 6px;
  }
  .frc-path {
    flex: 1;
    min-width: 80px;
  }
  .frc-callout {
    margin-left: 16px;
  }
}
</style>

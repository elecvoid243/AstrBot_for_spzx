<script setup lang="ts">
import { computed, inject } from "vue";
import { FileText } from "@lucide/vue";
import { LinkNode } from "markstream-vue";
import {
  OPEN_FILE_REFERENCE,
  parseFileReference,
} from "@/composables/fileReference";

const props = defineProps<{
  node: {
    type: "link";
    href: string;
    title: string | null;
    text: string;
    children: { type: string; raw: string }[];
    raw: string;
    loading?: boolean;
  };
  indexKey: number | string;
  customId?: string;
}>();
const open = inject(OPEN_FILE_REFERENCE, undefined);
// Link nodes exclude code blocks and inline code. Raw syntax excludes autolinks
// and the parser's repaired, unfinished streaming links.
const target = computed(() =>
  !props.node.loading && /^\[[\s\S]*\]\([\s\S]*\)$/.test(props.node.raw ?? "")
    ? parseFileReference(props.node.href)
    : null,
);

const reference = computed(() => {
  const file = target.value;
  const fragment = props.node.href.match(/#L\d+(?:-L\d+)?$/)?.[0] ?? "";
  let label =
    fragment && props.node.text.endsWith(fragment)
      ? props.node.text.slice(0, -fragment.length)
      : props.node.text;
  let normalizedLabel = label.replace(/\\/g, "/");
  const path = file?.path.replace(/\\/g, "/") ?? "";
  // Markstream rewrites __init__ as **init**. Restore literal underscores only
  // when that label exactly matches the destination, never for descriptive text.
  const literalLabel = normalizedLabel.replace(/\*\*/g, "__");
  if (path === literalLabel || path.endsWith(`/${literalLabel}`)) {
    label = label.replace(/\*\*/g, "__");
    normalizedLabel = literalLabel;
  }
  // Only split actual path labels; descriptive link text stays exactly as written.
  const isPath =
    normalizedLabel === path || path.endsWith(`/${normalizedLabel}`);
  const separator = isPath ? normalizedLabel.lastIndexOf("/") : -1;
  return {
    directory: separator < 0 ? "" : label.slice(0, separator + 1),
    name: isPath ? label.slice(separator + 1) : props.node.text,
    lines: file?.startLine
      ? file.endLine === file.startLine
        ? `L${file.startLine}`
        : `L${file.startLine}–${file.endLine}`
      : "",
    location: `${file?.path ?? ""}${fragment}`,
  };
});
</script>

<template>
  <a
    v-if="target"
    href="#"
    class="file-reference-link"
    :title="reference.location"
    :aria-label="`${reference.name} — ${reference.location}`"
    @click.stop.prevent="open?.(target)"
  >
    <FileText
      class="file-reference-icon"
      :size="14"
      :stroke-width="1.75"
      aria-hidden="true"
    />
    <span class="file-reference-label">
      <span v-if="reference.directory" class="file-reference-directory">{{
        reference.directory
      }}</span>
      <span class="file-reference-name">{{ reference.name }}</span>
    </span>
    <span
      v-if="reference.lines"
      class="file-reference-lines"
      aria-hidden="true"
      >{{ reference.lines }}</span
    >
  </a>
  <LinkNode v-else v-bind="props" />
</template>

<style scoped>
a.file-reference-link {
  display: inline-flex;
  align-items: center;
  gap: 0.4em;
  max-inline-size: min(calc(100% - 0.25em), 36rem);
  margin-inline: 0.125em;
  padding: 0.12em 0.5em;
  border: 1px solid rgba(var(--v-theme-primary, 60, 150, 202), 0.32);
  border-radius: 6px;
  background: rgba(var(--v-theme-primary, 60, 150, 202), 0.14);
  color: rgba(var(--v-theme-on-surface, 35, 40, 50), 0.88);
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 0.85em;
  font-weight: 500;
  line-height: 1.5;
  vertical-align: baseline;
  text-decoration: none;
  cursor: pointer;
  transition:
    background-color 150ms ease,
    border-color 150ms ease;
}

a.file-reference-link:hover,
a.file-reference-link:focus-visible {
  border-color: rgba(var(--v-theme-primary, 60, 150, 202), 0.65);
  background: rgba(var(--v-theme-primary, 60, 150, 202), 0.22);
  color: rgb(var(--v-theme-on-surface, 35, 40, 50));
  text-decoration: none;
}

a.file-reference-link:active {
  background: rgba(var(--v-theme-primary, 60, 150, 202), 0.3);
}

a.file-reference-link:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary, 60, 150, 202));
  outline-offset: 3px;
}

.file-reference-icon {
  flex: 0 0 auto;
  align-self: center;
  color: rgb(var(--v-theme-primary, 60, 150, 202));
}

.file-reference-label {
  display: inline-flex;
  align-items: baseline;
  min-inline-size: 0;
}

.file-reference-directory,
.file-reference-name {
  min-inline-size: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-reference-directory {
  flex: 0 10 auto;
  color: rgba(var(--v-theme-on-surface, 35, 40, 50), 0.58);
  font-weight: 400;
}

.file-reference-name {
  flex: 0 0 auto;
  max-inline-size: 100%;
}

.file-reference-lines {
  flex: 0 0 auto;
  padding-inline-start: 0.5em;
  border-inline-start: 1px solid
    rgba(var(--v-theme-on-surface, 35, 40, 50), 0.14);
  color: rgba(var(--v-theme-on-surface, 35, 40, 50), 0.7);
  font-size: 0.9em;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

@media (prefers-reduced-motion: reduce) {
  a.file-reference-link {
    transition: none;
  }
}
</style>

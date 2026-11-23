<script setup lang="ts">
import { computed, inject, unref, type Ref } from "vue";
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
const injectedReadOnly = inject<Ref<boolean> | boolean>("chatReadOnly", false);
const readOnly = computed(() => Boolean(unref(injectedReadOnly)));
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
    @click.stop.prevent="!readOnly && open?.(target)"
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
  <LinkNode
    v-else-if="!readOnly || /^(https?:|mailto:)/i.test(node.href)"
    v-bind="props"
  />
  <span v-else>{{ node.text }}</span>
</template>

<style scoped src="./fileReferenceChip.css"></style>

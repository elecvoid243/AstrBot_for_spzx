<script setup lang="ts">
import { computed, inject } from "vue";
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
</script>

<template>
  <a
    v-if="target"
    href="#"
    class="file-reference-link"
    :title="target.path"
    @click.stop.prevent="open?.(target)"
    >{{ node.text }}</a
  >
  <LinkNode v-else v-bind="props" />
</template>

<!-- Author: elecvoid243, 2026-07-30
     Updated 2026-10-01 (elecvoid243): skill-request block support.
     Message text layout is
     [userText][comments block][references block][skills block]
     (send-time concat in Chat.vue). Each parser scans the FULL text and
     returns only the text BEFORE its own marker, so the earliest block's
     userText is the true display prefix: comments > references > skills.
     Historical messages get the rich rendering automatically because all
     blocks are parsed back out of the stored message text. -->
<template>
  <div class="user-plain-part">
    <template v-if="review || references || skillRequest">
      <div v-if="displayText" class="plain-content">
        {{ displayText }}
      </div>
      <FileReviewCommentsCard v-if="review" :review="review" />
      <FileReferencesCard v-if="references" :block="references" />
      <SkillRequestCard v-if="skillRequest" :block="skillRequest" />
    </template>
    <div v-else class="plain-content">{{ text }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { parseFileReviewComments } from "@/utils/parseFileReviewComments";
import { parseFileReferences } from "@/utils/parseFileReferences";
import { parseSkillRequests } from "@/utils/parseSkillRequests";
import FileReviewCommentsCard from "./FileReviewCommentsCard.vue";
import FileReferencesCard from "./FileReferencesCard.vue";
import SkillRequestCard from "./SkillRequestCard.vue";

const props = defineProps<{ text: string }>();

// computeds cache on props.text, so each parse runs once per text change
// rather than on every re-render of the (potentially long) message list.
// Every parser scans the FULL text: a later block's marker sits after the
// earlier blocks, so feeding an earlier parser's userText here would make
// the later card silently vanish (2026-08-13 combined-block fix).
const review = computed(() => parseFileReviewComments(props.text));
const references = computed(() => parseFileReferences(props.text));
const skillRequest = computed(() => parseSkillRequests(props.text));
// The free-form text above the cards: block order is fixed
// (comments → references → skills) and each parser's userText is
// everything before its own marker, so the EARLIEST present block holds
// the clean prefix; later parsers' userText still contains the earlier
// raw blocks and must not be re-rendered as plain text.
const displayText = computed(() => {
  if (review.value) return review.value.userText;
  if (references.value) return references.value.userText;
  return skillRequest.value?.userText ?? "";
});
</script>

<style scoped>
/* Mirrors ChatMessageList's .plain-content (the parent's scoped rule does
   not reach into this child, so it is re-declared here). */
.plain-content {
  white-space: pre-wrap;
}
</style>

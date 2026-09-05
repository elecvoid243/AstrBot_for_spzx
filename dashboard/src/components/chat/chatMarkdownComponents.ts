import { setCustomComponents } from "markstream-vue";
import "markstream-vue/index.css";
import HtmlGenUiNode from "@/components/chat/message_list_comps/HtmlGenUiNode.vue";
import RefNode from "@/components/chat/message_list_comps/RefNode.vue";
import ThreadNode from "@/components/chat/message_list_comps/ThreadNode.vue";
import ShikiCodeBlock from "@/components/shared/ShikiCodeBlock.vue";
import ThemeAwareMarkdownCodeBlock from "@/components/shared/ThemeAwareMarkdownCodeBlock.vue";

export const CHAT_MARKDOWN_CUSTOM_TAGS: string[] = ["ref", "html-genui"];

export function registerChatMarkdownComponents() {
  setCustomComponents("chat-message", {
    ref: RefNode,
    thread: ThreadNode,
    "html-genui": HtmlGenUiNode,
    // 2026-08-01: restored the library's native code block for chat
    // (icon/header/collapse/font-size/expand + built-in shiki render) —
    // it was never broken here; ShikiCodeBlock only fixes the
    // document-view path, which has no registration of its own.
    code_block: ThemeAwareMarkdownCodeBlock,
  });
}

/**
 * 2026-07-31 shiki-code-block: MarkdownView (custom-id "document-view")
 * renders markdown for the document manager / workspace preview and the
 * readme dialog. This path has no custom code block by default (it fell
 * back to the library's unstyled bare <pre>), so register the
 * lightweight ShikiCodeBlock here; setCustomComponents is idempotent
 * per custom-id, so calling this from every MarkdownView setup is safe.
 */
export function registerDocumentViewMarkdownComponents() {
  setCustomComponents("document-view", {
    code_block: ShikiCodeBlock,
  });
}

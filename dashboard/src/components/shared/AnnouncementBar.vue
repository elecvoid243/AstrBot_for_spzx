<template>
  <!-- Rendered on every page as a v-system-bar layout item pinned to the very
       top of the viewport, ABOVE the v-app-bar. order="-1" sorts it before the
       app bar in Vuetify's layer stack, so the header, sidebars and v-main
       padding are pushed down by the bar height automatically (the chat page
       gets the matching extra padding from FullLayout). -->
  <v-system-bar
    v-if="visible && !collapsed"
    order="-1"
    :height="36"
    class="announcement-bar"
    role="region"
    :aria-label="tm('announcementBar.label')"
  >
    <div class="ann-left">
      <v-icon size="18" class="mr-2">mdi-bullhorn-variant</v-icon>
      <span class="ann-label">{{ tm("announcementBar.label") }}</span>
    </div>

    <div
      class="ann-middle"
      :title="data?.title"
      @click="open = true"
      @mouseenter="hover = true"
      @mouseleave="hover = false"
    >
      <div class="ann-track" :class="{ paused: hover }">
        <span
          v-for="copy in 2"
          :key="copy"
          class="ann-text"
          :aria-hidden="copy === 2 ? 'true' : undefined"
        >
          <template v-for="(part, index) in summary" :key="index">
            <img
              v-if="part.src"
              :src="part.src"
              :alt="part.text"
              class="ann-image"
            />
            <span v-else>{{ part.text }}</span>
          </template>
        </span>
      </div>
    </div>

    <div class="ann-right">
      <v-btn
        variant="text"
        density="comfortable"
        size="small"
        icon
        :aria-label="tm('announcementBar.viewDetail')"
        @click="open = true"
      >
        <v-icon size="18">mdi-open-in-new</v-icon>
      </v-btn>
      <v-btn
        variant="text"
        density="comfortable"
        size="small"
        icon
        :aria-label="tm('announcementBar.closeAriaLabel')"
        @click="collapsed = true"
      >
        <v-icon size="18">mdi-close</v-icon>
      </v-btn>
    </div>
  </v-system-bar>

  <v-dialog v-if="visible" v-model="open" max-width="720" scrollable>
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ data?.title }}
      </v-card-title>
      <v-card-text class="pa-4 pt-0">
        <MarkdownRender
          :content="data?.content || ''"
          :typewriter="false"
          class="ann-dialog-markdown markdown-content"
        />
      </v-card-text>
      <v-card-actions class="px-4 pb-4">
        <v-spacer />
        <v-btn color="primary" variant="text" @click="open = false">
          {{ tm("announcementBar.close") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
/**
 * 顶部滚动公告条 (可折叠).
 *
 * 数据流: useAnnouncement (composable) → 调 /api/system/announcement
 *   - 数据源: AstrBot Core 代理的更新服务器 /announcement
 *   - 无公告 / 加载失败时: visible = false (整个组件不挂载)
 *
 * 渲染形态 (所有页面一致):
 *   - v-system-bar 布局项, order="-1" 使其在 Vuetify 图层栈中排在
 *     v-app-bar 之前, 固定在视口最顶端 (toolbar 上方).
 *   - Bot 页面: v-app-bar / v-navigation-drawer / v-main 的偏移由布局
 *     系统自动下推 36px.
 *   - Chat 页面: v-app-bar 同样自动下移; v-main 需要的 36px 附加 padding
 *     由 FullLayout 的 chat-main-announcement 类补齐 (chat 侧栏随之下移,
 *     brand 不再被横条遮挡).
 *
 * 折叠/展开行为 (collapsed 存于 useAnnouncement 单例, 与 VerticalHeader 联动,
 * 不持久化, 刷新后重置):
 *   - 右侧 ✕ 按钮: 折叠 (collapsed = true), 两种页面均收起横条.
 *   - 折叠态喇叭按钮: 由 VerticalHeader 渲染在顶栏上 ("Bot"/"Chat" 切换按钮
 *     旁), 点击恢复完整横条 (collapsed = false).
 *   - 刷新页面后默认展开 (与"完全关闭"语义不同).
 *
 * 交互:
 *   - 鼠标 hover 横条中部: 暂停 marquee 滚动
 *   - 点击条身 / 右侧 ⤴ 按钮: 打开 v-dialog 查看完整 Markdown
 *   - 右侧 ✕ 按钮: 折叠
 *   - 顶栏喇叭按钮: 展开
 *
 * 作者: AstrBot Agent Harness
 * 时间: 2026-06-12
 */
import { computed, ref } from "vue";
import { useAnnouncement } from "@/composables/useAnnouncement";
import { useModuleI18n } from "@/i18n/composables";
import { MarkdownRender } from "markstream-vue";
import MarkdownIt from "markdown-it";
import "markstream-vue/index.css";

const { tm } = useModuleI18n("features/welcome");
const { data, collapsed } = useAnnouncement();

const hover = ref(false);
const open = ref(false);

const visible = computed(() => !!data.value);
const markdown = new MarkdownIt({ html: false });

const summary = computed(() => {
  if (!data.value) return [];
  const title = (data.value.title || "").trim();
  const parts: { text: string; src?: string }[] = [];
  if (title) parts.push({ text: title });
  let remaining = 200 - title.length;
  const tokens = markdown.parse(data.value.content || "", {});
  for (const token of tokens) {
    if (token.type === "fence" || token.type === "code_block") continue;
    if (token.type !== "inline" || !token.children) continue;
    if (parts.length)
      parts.push({ text: parts.length === 1 && title ? "　·　" : " " });
    for (const child of token.children) {
      if (child.type === "image") {
        const src = child.attrGet("src") || "";
        if (/^https?:\/\//i.test(src)) parts.push({ text: child.content, src });
        else if (child.content) parts.push({ text: child.content });
      } else if (
        ["text", "code_inline", "softbreak", "hardbreak"].includes(
          child.type,
        ) &&
        remaining > 0
      ) {
        const value = child.type.endsWith("break") ? " " : child.content;
        parts.push({ text: value.slice(0, remaining) });
        remaining -= value.length;
        if (remaining <= 0) parts.push({ text: "…" });
      }
    }
  }
  return parts;
});
</script>

<style scoped>
.announcement-bar {
  /* v-system-bar 布局项: 定位 (fixed, top: 0, 全宽, zIndex) 由 Vuetify
     布局系统的内联样式提供, 这里只负责外观. */
  display: flex;
  align-items: center;
  height: 36px;
  padding: 0 12px;
  background: linear-gradient(90deg, #fff8e1 0%, #fffbf0 50%, #fff8e1 100%);
  border-bottom: 1px solid #ffe082;
  color: #b45309;
  font-size: 13px;
  box-shadow: 0 1px 3px rgba(180, 83, 9, 0.08);
}

/* v-system-bar 默认把图标调淡 (medium-emphasis), 恢复预期外观. */
.announcement-bar :deep(.v-icon) {
  opacity: 1;
}

.ann-left {
  display: flex;
  align-items: center;
  min-width: 96px;
  flex-shrink: 0;
}

.ann-label {
  font-weight: 700;
}

.ann-middle {
  flex: 1;
  overflow: hidden;
  margin: 0 12px;
  cursor: pointer;
  position: relative;
  min-width: 0;
}

.ann-track {
  display: inline-flex;
  white-space: nowrap;
  animation: ann-marquee 40s linear infinite;
  will-change: transform;
}

.ann-track.paused {
  animation-play-state: paused;
}

.ann-text {
  display: inline-flex;
  align-items: center;
  padding-right: 64px;
  user-select: none;
}

.ann-image {
  max-height: 28px;
  max-width: 120px;
  object-fit: contain;
  margin: 0 4px;
}

.ann-right {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

@keyframes ann-marquee {
  from {
    transform: translateX(0);
  }
  to {
    transform: translateX(-50%);
  }
}

.ann-dialog-markdown {
  line-height: 1.7;
}

.ann-dialog-markdown :deep(img) {
  max-width: 100%;
  height: auto;
}
</style>

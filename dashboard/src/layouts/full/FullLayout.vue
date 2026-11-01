<script setup lang="ts">
import { RouterView, useRoute } from "vue-router";
import { ref, onMounted, computed, watch } from "vue";
import VerticalSidebarVue from "./vertical-sidebar/VerticalSidebar.vue";
import VerticalHeaderVue from "./vertical-header/VerticalHeader.vue";
import ReadmeDialog from "@/components/shared/ReadmeDialog.vue";
import AnnouncementBar from "@/components/shared/AnnouncementBar.vue";
import Chat from "@/components/chat/Chat.vue";
import { useAuthStore } from "@/stores/auth";
const auth = useAuthStore();
import { useAnnouncement } from "@/composables/useAnnouncement";
import { useCustomizerStore } from "@/stores/customizer";
import { useRouterLoadingStore } from "@/stores/routerLoading";
import { useCommonStore } from "@/stores/common";
import { statsApi } from "@/api/v1";
import { useI18n } from "@/i18n/composables";

const FIRST_NOTICE_SEEN_KEY = "astrbot:first_notice_seen:v1";

const customizer = useCustomizerStore();
const commonStore = useCommonStore();
const { locale } = useI18n();
const route = useRoute();
const routerLoadingStore = useRouterLoadingStore();
const isCurrentChatRoute = computed(
  () => route.path === "/chat" || route.path.startsWith("/chat/"),
);
const isPluginViewRoute = computed(() =>
  route.path.startsWith("/plugin-view/"),
);
const isProviderPageRoute = computed(() => route.path === "/providers");
const isPlatformPageRoute = computed(() => route.path === "/platforms");
const isViewportLockedRoute = computed(
  () =>
    isCurrentChatRoute.value ||
    isProviderPageRoute.value ||
    isPlatformPageRoute.value,
);
const isFullScreenRoute = computed(
  () => isCurrentChatRoute.value || isPluginViewRoute.value,
);
const shouldMountChat = ref(isCurrentChatRoute.value);

const showSidebar = computed(() => auth.role === "admin" && !isCurrentChatRoute.value);

// Expanded announcement bar: the v-system-bar layout item is rendered by
// AnnouncementBar on every page. On chat pages v-main normally forces
// padding-top: 0 (the chat header is an absolute overlay and Chat.vue
// reserves its own 50px), so the 36px the layout system pushes down must
// be re-applied explicitly to keep the chat sidebar/content below the bar.
const { data: announcementData, collapsed: announcementCollapsed } =
  auth.role === "admin" ? useAnnouncement() : { data: ref(null), collapsed: ref(true) };
const announcementActive = computed(
  () => !!announcementData.value && !announcementCollapsed.value,
);

const showFirstNoticeDialog = ref(false);

watch(isCurrentChatRoute, (isChatRoute) => {
  if (isChatRoute) {
    shouldMountChat.value = true;
  }
});

const maybeShowFirstNotice = async () => {
  if (localStorage.getItem(FIRST_NOTICE_SEEN_KEY) === "1") {
    return;
  }

  try {
    const response = await statsApi.firstNotice(locale.value);
    if (response.data.status !== "ok") {
      return;
    }

    const content = response.data?.data?.content;
    if (typeof content === "string" && content.trim().length > 0) {
      showFirstNoticeDialog.value = true;
      return;
    }

    localStorage.setItem(FIRST_NOTICE_SEEN_KEY, "1");
  } catch (error) {
    console.error("Failed to load first notice:", error);
  }
};

const onFirstNoticeDialogUpdate = (visible: boolean) => {
  showFirstNoticeDialog.value = visible;
  if (!visible) {
    localStorage.setItem(FIRST_NOTICE_SEEN_KEY, "1");
  }
};

onMounted(() => {
  if (auth.role !== "admin") return;
  setTimeout(async () => {
    try {
      const response = await statsApi.version();
      if (response.data.status === "ok") {
        commonStore.setAstrBotVersion(
          response.data.data?.version,
          response.data.data?.dashboard_version,
        );
      }
    } catch (error) {
      console.error("Failed to load version info:", error);
    }
    await maybeShowFirstNotice();
  }, 1000);
});
</script>

<template>
  <v-locale-provider>
    <v-app
      :theme="useCustomizerStore().uiTheme"
      :class="[
        customizer.fontTheme,
        customizer.mini_sidebar ? 'mini-sidebar' : '',
        customizer.inputBg ? 'inputWithbg' : '',
      ]"
    >
      <v-progress-linear
        v-if="routerLoadingStore.isLoading"
        :model-value="routerLoadingStore.progress"
        color="primary"
        height="2"
        fixed
        top
        style="z-index: 9999; position: absolute; opacity: 0.3"
      />
      <VerticalHeaderVue v-if="auth.role === 'admin'" />
      <VerticalSidebarVue v-if="showSidebar" />
      <v-main
        :class="{
          'chat-main': isCurrentChatRoute,
          'chat-main-announcement': isCurrentChatRoute && announcementActive,
        }"
        :style="{
          height: isViewportLockedRoute ? '100vh' : undefined,
          overflow: isViewportLockedRoute ? 'hidden' : undefined,
        }"
      >
        <!-- 顶部滚动公告条: AnnouncementBar 在所有页面渲染为 v-system-bar
             布局项 (order=-1), 固定在视口最顶端并把 v-app-bar 下推; bot 页面
             的 drawer / v-main padding 由布局系统自动让位, chat 页面的额外
             36px padding 由下方 chat-main-announcement 类补齐. -->
        <AnnouncementBar v-if="auth.role === 'admin'" />
        <v-container
          fluid
          class="page-wrapper"
          :class="{
            'chat-mode-container': isCurrentChatRoute,
            'viewport-locked-container':
              isProviderPageRoute || isPlatformPageRoute,
          }"
          :style="{
            height:
              isFullScreenRoute || isProviderPageRoute || isPlatformPageRoute
                ? '100%'
                : 'calc(100% - 8px)',
            padding: isFullScreenRoute ? '0' : undefined,
            minHeight:
              isFullScreenRoute || isProviderPageRoute || isPlatformPageRoute
                ? 'unset'
                : undefined,
          }"
        >
          <div
            :style="{
              height: '100%',
              width: '100%',
              overflow: isViewportLockedRoute ? 'hidden' : undefined,
              position: isPluginViewRoute ? 'relative' : undefined,
            }"
          >
            <div
              v-if="shouldMountChat"
              v-show="isCurrentChatRoute"
              style="height: 100%; width: 100%; overflow: hidden"
            >
              <Chat v-if="auth.role === 'admin' || auth.role === 'user'" :active="isCurrentChatRoute" />
            </div>
            <RouterView v-if="!isCurrentChatRoute" />
          </div>
        </v-container>
      </v-main>

      <ReadmeDialog
        :show="showFirstNoticeDialog"
        mode="first-notice"
        @update:show="onFirstNoticeDialogUpdate"
      />
    </v-app>
  </v-locale-provider>
</template>

<style scoped>
.chat-mode-container {
  min-height: unset !important;
  height: 100% !important;
  overflow: hidden !important;
}

.viewport-locked-container {
  min-height: unset !important;
  height: 100% !important;
  overflow: hidden !important;
}

.chat-main {
  padding-top: 0 !important;
}

/* 公告条展开时 (v-system-bar 布局项占据视口顶部 36px), chat 内容随之下移;
   36px 需与 AnnouncementBar 的 :height="36" 保持一致. 定义在 .chat-main
   之后, 同为 !important 时按源顺序覆盖. */
.chat-main-announcement {
  padding-top: 36px !important;
}
</style>

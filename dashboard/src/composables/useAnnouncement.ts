/**
 * 公告数据 Composable.
 *
 * 数据源: AstrBot Core 代理的更新服务器公告接口 (/api/system/announcement).
 * 该接口会透传更新服务器 /announcement 的状态码:
 *   - 200: 返回公告 JSON
 *   - 404: 无公告 / 公告已禁用
 *   - 502/503: 后端代理 / 上游异常
 *
 * 设计原则:
 *   - 静默失败: 任何错误都视为"无公告", 公告条不显示.
 *   - Polling: fetch once on startup, then silently re-fetch every
 *     POLL_INTERVAL_MS so announcement updates appear without a page
 *     refresh. Transient poll failures keep the last known data; only an
 *     upstream 404 ("announcement removed") clears the bar.
 *   - 集中缓存: 暴露 module 级单例, 多个组件共享同一份数据.
 *   - collapsed 为共享 UI 状态 (不持久化, 刷新后重置):
 *     AnnouncementBar (公告条本体) 与 VerticalHeader (顶栏喇叭按钮)
 *     通过它联动折叠/展开.
 *
 * 作者: AstrBot Agent Harness
 * 时间: 2026-06-12
 */
import { ref } from "vue";
import axios from "axios";

export interface AnnouncementData {
  title: string;
  content: string;
  enabled: boolean;
  version: number;
  published_at?: string;
  created_at?: string;
}

interface AnnouncementState {
  data: import("vue").Ref<AnnouncementData | null>;
  loading: import("vue").Ref<boolean>;
  error: import("vue").Ref<string | null>;
  reload: () => Promise<void>;
  collapsed: import("vue").Ref<boolean>;
}

// --- module 级单例: 多组件共享同一份公告数据 ---
let _singleton: AnnouncementState | null = null;

// Re-fetch interval for the announcement bar (15 minutes).
const POLL_INTERVAL_MS = 15 * 60 * 1000;

function createAnnouncementState(): AnnouncementState {
  const data = ref<AnnouncementData | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const collapsed = ref(false);

  async function load(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const res = await axios.get("/api/system/announcement", {
        timeout: 5000,
      });
      const payload = res.data?.data ?? res.data;
      if (payload && typeof payload === "object" && payload.title) {
        data.value = payload as AnnouncementData;
      } else {
        // 200 但 payload 异常: 静默置空
        data.value = null;
      }
    } catch (e: any) {
      // 404 (公告已被撤下): 明确的"无公告", 清空.
      // 其余 (网络错误 / 502 / 503 / 超时): 瞬时故障, 轮询场景下保留
      // 已展示的公告, 避免公告条闪没; 首次加载时本来就没有数据.
      error.value = e?.response?.data?.message ?? e?.message ?? "unknown";
      if (!data.value || e?.response?.status === 404) {
        data.value = null;
      }
    } finally {
      loading.value = false;
    }
  }

  // 立即触发首次加载
  void load();

  // Singleton lives for the whole SPA session, so the timer is never
  // cleared; poll failures are handled silently inside load().
  window.setInterval(() => void load(), POLL_INTERVAL_MS);

  return { data, loading, error, reload: load, collapsed };
}

export function useAnnouncement(): AnnouncementState {
  if (!_singleton) {
    _singleton = createAnnouncementState();
  }
  return _singleton;
}

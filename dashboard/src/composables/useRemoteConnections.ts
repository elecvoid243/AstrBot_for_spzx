import { onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import {
  remoteConnectionsApi,
  type RemoteConnection,
  type RemoteSession,
} from "@/api/remoteConnections";
import { accountSessionRevision } from "@/utils/accountSession";

export function useRemoteConnections() {
  const enabled = ref(false);
  const connections = ref<RemoteConnection[]>([]);
  const sessions = reactive<Record<string, RemoteSession[]>>({});
  const status = reactive<Record<string, string>>({});
  const versions = reactive<Record<string, number>>({});
  let generation = 0;
  let listVersion = 0;
  let timer: ReturnType<typeof setInterval>;

  async function refreshConnection(id: string) {
    const revision = accountSessionRevision.value;
    const current = generation;
    const version = (versions[id] = (versions[id] || 0) + 1);
    try {
      const response = await remoteConnectionsApi.sessions(id);
      if (
        revision !== accountSessionRevision.value ||
        current !== generation ||
        versions[id] !== version ||
        !enabled.value ||
        !connections.value.some((c) => c.connection_id === id)
      )
        return;
      sessions[id] = response.data.data;
      status[id] = "ready";
    } catch (error: any) {
      if (
        revision !== accountSessionRevision.value ||
        current !== generation ||
        versions[id] !== version ||
        !enabled.value ||
        !connections.value.some((c) => c.connection_id === id)
      )
        return;
      sessions[id] = [];
      status[id] = error.response?.data?.data?.code || "remote_offline";
    }
  }

  async function refresh() {
    const version = ++listVersion;
    const revision = accountSessionRevision.value;
    const current = generation;
    try {
      const response = await remoteConnectionsApi.list();
      if (
        revision !== accountSessionRevision.value ||
        current !== generation ||
        version !== listVersion
      )
        return;
      enabled.value = response.data.data.enabled;
      for (const row of response.data.data.connections) {
        if (
          connections.value.find((c) => c.connection_id === row.connection_id)
            ?.revision !== row.revision
        ) {
          delete sessions[row.connection_id];
          delete status[row.connection_id];
          versions[row.connection_id] = (versions[row.connection_id] || 0) + 1;
        }
      }
      connections.value = response.data.data.connections;
      const ids = new Set(connections.value.map((c) => c.connection_id));
      for (const id of Object.keys(sessions))
        if (!ids.has(id) || !enabled.value) {
          delete sessions[id];
          delete status[id];
        }
      if (enabled.value)
        await Promise.all(
          connections.value.map((c) => refreshConnection(c.connection_id)),
        );
    } catch {
      if (
        revision !== accountSessionRevision.value ||
        current !== generation ||
        version !== listVersion
      )
        return;
      connections.value = [];
      enabled.value = false;
      for (const id of Object.keys(sessions)) {
        delete sessions[id];
        delete status[id];
      }
    }
  }

  function invalidate() {
    generation += 1;
    connections.value = [];
    enabled.value = false;
    for (const id of Object.keys(sessions)) {
      delete sessions[id];
      delete status[id];
    }
  }
  watch(accountSessionRevision, invalidate, { flush: "sync" });
  onMounted(() => {
    void refresh();
    timer = setInterval(() => {
      void refresh();
    }, 15000);
    window.addEventListener("focus", refresh);
  });
  onBeforeUnmount(() => {
    invalidate();
    clearInterval(timer);
    window.removeEventListener("focus", refresh);
  });
  return {
    enabled,
    connections,
    sessions,
    status,
    refresh,
    refreshConnection,
    invalidate,
  };
}

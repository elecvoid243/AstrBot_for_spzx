import { ref } from "vue";
import { httpClient } from "@/api/http";

type Kind = "role" | "workflow" | "autonomous";
export type Draft = {
  asset_id: string;
  revision: number;
  published_revision?: number | null;
  body: any;
  base_version: number | null;
};

const url = (kind: Kind, id?: string) =>
  `/api/v1/agent_drafts/${kind}${id ? `/${encodeURIComponent(id)}` : ""}`;
export const draftApi = {
  async list(kind: Kind): Promise<Draft[]> {
    return (await httpClient.get(url(kind))).data.data;
  },
  async get(kind: Kind, id: string): Promise<Draft> {
    return (await httpClient.get(url(kind, id))).data.data;
  },
  async create(
    kind: Kind,
    body: unknown,
    asset_id: string | null,
  ): Promise<Draft> {
    return (await httpClient.post(url(kind), { body, asset_id })).data.data;
  },
  async update(
    kind: Kind,
    id: string,
    body: unknown,
    expected_revision: number,
  ): Promise<Draft> {
    return (await httpClient.put(url(kind, id), { body, expected_revision }))
      .data.data;
  },
  async latest(kind: Kind, id: string) {
    const path = kind === "role" ? "agent_roles" : "workflow_assets";
    return (await httpClient.get(`/api/v1/${path}/${encodeURIComponent(id)}`))
      .data.data;
  },
  async rebase(
    kind: Kind,
    id: string,
    expected_revision: number,
    published_version: number,
  ): Promise<Draft> {
    return (
      await httpClient.post(`${url(kind, id)}/rebase`, {
        expected_revision,
        published_version,
      })
    ).data.data;
  },
  async publish(kind: Kind, id: string, revision: number) {
    return (await httpClient.post(`${url(kind, id)}/publish`, { revision }))
      .data.data;
  },
};

// Each editing session owns its queue; responses can never mutate a later session.
export function useAssetDraft(kind: Kind) {
  const status = ref<"saving" | "saved" | "failed" | "conflict">("saved");
  const reviewed = ref<any>(null);
  const busy = ref(false);
  const baseVersion = ref<number | null>(null);
  let generation = 0;
  let session: {
    draft: Draft;
    confirmed: string;
    latest: string;
    running?: Promise<void>;
  } | null = null;
  async function open(id: string | null, initial: unknown) {
    const token = ++generation;
    session = null;
    busy.value = false;
    reviewed.value = null;
    baseVersion.value = null;
    status.value = "saving";
    try {
      const existing = id
        ? (await draftApi.list(kind)).find((item) => item.asset_id === id)
        : undefined;
      const draft = existing ?? (await draftApi.create(kind, initial, id));
      if (token !== generation) throw new Error("Draft session changed");
      const confirmed = JSON.stringify(draft.body);
      session = { draft, confirmed, latest: confirmed };
      baseVersion.value = draft.base_version;
      status.value = "saved";
      return draft;
    } catch (error) {
      if (token === generation) status.value = "failed";
      throw error;
    }
  }
  function change(body: unknown) {
    if (!session) return;
    session.latest = JSON.stringify(body);
    if (!busy.value && status.value !== "failed" && status.value !== "conflict")
      void flush().catch(() => {});
  }
  async function flush() {
    const current = session;
    if (!current) throw new Error("Draft is not open");
    if (current.running) {
      await current.running;
      if (session !== current) throw new Error("Draft session changed");
      return current.draft;
    }
    current.running = (async () => {
      while (current.latest !== current.confirmed) {
        const snapshot = current.latest;
        if (session === current) status.value = "saving";
        try {
          const saved = await draftApi.update(
            kind,
            current.draft.asset_id,
            JSON.parse(snapshot),
            current.draft.revision,
          );
          current.draft = saved;
          current.confirmed = snapshot;
        } catch (error: any) {
          if (session === current)
            status.value =
              error?.response?.status === 409 ? "conflict" : "failed";
          throw error;
        }
      }
      if (session === current) status.value = "saved";
    })();
    try {
      await current.running;
    } finally {
      current.running = undefined;
    }
    if (session !== current) throw new Error("Draft session changed");
    return current.draft;
  }
  async function retry() {
    if (status.value === "conflict") return;
    await flush();
  }
  async function reload() {
    if (!session) throw new Error("Draft is not open");
    const current = session;
    const draft = await draftApi.get(kind, current.draft.asset_id);
    if (session === current) {
      const confirmed = JSON.stringify(draft.body);
      session = { draft, confirmed, latest: confirmed };
      baseVersion.value = draft.base_version;
      reviewed.value = null;
      status.value = "saved";
    }
    return draft;
  }
  async function reviewPublished() {
    const current = session;
    if (!current) throw new Error("Draft is not open");
    const value = await draftApi.latest(kind, current.draft.asset_id);
    if (session !== current) throw new Error("Draft session changed");
    reviewed.value = value;
  }
  async function rebase() {
    const current = session;
    const version = reviewed.value?.version;
    if (!current || !version)
      throw new Error("Review the latest version first");
    busy.value = true;
    try {
      status.value = "saved";
      await flush();
      if (session !== current) throw new Error("Draft session changed");
      const updated = await draftApi.rebase(
        kind,
        current.draft.asset_id,
        current.draft.revision,
        version,
      );
      if (session !== current) throw new Error("Draft session changed");
      current.draft = updated;
      baseVersion.value = updated.base_version;
      reviewed.value = null;
      status.value = "saved";
    } catch (error: any) {
      if (session === current)
        status.value = error?.response?.status === 409 ? "conflict" : "failed";
      throw error;
    } finally {
      if (session === current) {
        busy.value = false;
        if (status.value === "saved" && current.latest !== current.confirmed)
          void flush().catch(() => {});
      }
    }
  }
  async function publish() {
    const current = session;
    if (!current || status.value === "failed" || status.value === "conflict")
      throw new Error("Draft is not saved");
    await flush();
    if (session !== current) throw new Error("Draft session changed");
    try {
      return await draftApi.publish(
        kind,
        current.draft.asset_id,
        current.draft.revision,
      );
    } catch (error: any) {
      if (session === current && error?.response?.status === 409)
        status.value = "conflict";
      throw error;
    }
  }

  function close() {
    generation++;
    session = null;
    busy.value = false;
  }
  return {
    status,
    reviewed,
    baseVersion,
    busy,
    open,
    change,
    flush,
    retry,
    reload,
    publish,
    reviewPublished,
    rebase,
    close,
  };
}

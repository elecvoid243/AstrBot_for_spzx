import { mount } from "@vue/test-utils";
import { defineComponent, nextTick } from "vue";
import { afterEach, expect, it, vi } from "vitest";
import { useRemoteConnections } from "./useRemoteConnections";
import { remoteConnectionsApi } from "@/api/remoteConnections";
import { resetAccountSession } from "@/utils/accountSession";
vi.mock("@/api/remoteConnections", () => ({
  remoteConnectionsApi: { list: vi.fn(), sessions: vi.fn() },
}));
const settle = async () => {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await nextTick();
};
afterEach(() => {
  vi.resetAllMocks();
});
it("separates equal project/session IDs and clears revoked or disabled lists", async () => {
  vi.mocked(remoteConnectionsApi.list).mockResolvedValue({
    data: {
      data: {
        enabled: true,
        connections: [
          { connection_id: "a", name: "same" },
          { connection_id: "b", name: "same" },
        ],
      },
    },
  } as any);
  vi.mocked(remoteConnectionsApi.sessions).mockImplementation(
    async (id) =>
      ({
        data: {
          data: [{ session_id: "same", project_id: "same", display_name: id }],
        },
      }) as any,
  );
  let remote!: ReturnType<typeof useRemoteConnections>;
  const wrapper = mount(
    defineComponent({
      setup() {
        remote = useRemoteConnections();
        return () => null;
      },
    }),
  );
  await settle();
  expect(remote.sessions.a[0].display_name).toBe("a");
  expect(remote.sessions.b[0].display_name).toBe("b");
  vi.mocked(remoteConnectionsApi.sessions).mockRejectedValueOnce({
    response: { data: { data: { code: "remote_unauthorized" } } },
  });
  await remote.refreshConnection("a");
  expect(remote.sessions.a).toEqual([]);
  expect(remote.sessions.b).toHaveLength(1);
  vi.mocked(remoteConnectionsApi.list).mockResolvedValue({
    data: { data: { enabled: false, connections: [{ connection_id: "a" }] } },
  } as any);
  await remote.refresh();
  expect(Object.keys(remote.sessions)).toHaveLength(0);
  resetAccountSession(false);
  expect(remote.connections.value).toEqual([]);
  wrapper.unmount();
});
it("discards late discovery after account switch", async () => {
  let finish!: (value: any) => void;
  vi.mocked(remoteConnectionsApi.list).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }) as any,
  );
  let remote!: ReturnType<typeof useRemoteConnections>;
  const wrapper = mount(
    defineComponent({
      setup() {
        remote = useRemoteConnections();
        return () => null;
      },
    }),
  );
  resetAccountSession(false);
  finish({
    data: { data: { enabled: true, connections: [{ connection_id: "old" }] } },
  });
  await settle();
  expect(remote.connections.value).toEqual([]);
  expect(remote.enabled.value).toBe(false);
  wrapper.unmount();
});

it("discards an older concurrent connection-list response", async () => {
  vi.mocked(remoteConnectionsApi.list).mockResolvedValueOnce({
    data: { data: { enabled: true, connections: [] } },
  } as any);
  let remote!: ReturnType<typeof useRemoteConnections>;
  const wrapper = mount(
    defineComponent({
      setup() {
        remote = useRemoteConnections();
        return () => null;
      },
    }),
  );
  await settle();
  let finish!: (value: any) => void;
  vi.mocked(remoteConnectionsApi.list)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }) as any,
    )
    .mockResolvedValueOnce({
      data: { data: { enabled: false, connections: [] } },
    } as any);
  const old = remote.refresh();
  await remote.refresh();
  finish({
    data: { data: { enabled: true, connections: [{ connection_id: "old" }] } },
  });
  await old;
  expect(remote.enabled.value).toBe(false);
  expect(remote.connections.value).toEqual([]);
  wrapper.unmount();
});
it("invalidates sessions immediately when a saved binding revision changes", async () => {
  vi.mocked(remoteConnectionsApi.list).mockResolvedValueOnce({
    data: {
      data: {
        enabled: true,
        connections: [{ connection_id: "a", revision: 1 }],
      },
    },
  } as any);
  vi.mocked(remoteConnectionsApi.sessions).mockResolvedValueOnce({
    data: { data: [{ session_id: "same", project_id: "same" }] },
  } as any);
  let remote!: ReturnType<typeof useRemoteConnections>;
  const wrapper = mount(
    defineComponent({
      setup() {
        remote = useRemoteConnections();
        return () => null;
      },
    }),
  );
  await settle();
  let finish!: (value: any) => void;
  vi.mocked(remoteConnectionsApi.list).mockResolvedValueOnce({
    data: {
      data: {
        enabled: true,
        connections: [{ connection_id: "a", revision: 2 }],
      },
    },
  } as any);
  vi.mocked(remoteConnectionsApi.sessions).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }) as any,
  );
  const pending = remote.refresh();
  await settle();
  expect(remote.sessions.a).toBeUndefined();
  expect(remote.status.a).toBeUndefined();
  finish({
    data: { data: [{ session_id: "same", display_name: "new source" }] },
  });
  await pending;
  expect(remote.sessions.a[0].display_name).toBe("new source");
  wrapper.unmount();
});

// Author: elecvoid243
// Date: 2026-09-01
//
// The flat session list in Chat.vue excludes project sessions (backend
// `exclude_project_sessions=True`), so ProjectList is their only sidebar
// renderer. These tests pin the ask_user_choice highlight contract on the
// project session rows: rows and pulsing dots must react to the shared
// interactiveChoiceAttention store exactly like the flat list does.

import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createVuetify } from "vuetify";
import {
  VBtn,
  VCard,
  VCheckboxBtn,
  VDivider,
  VList,
  VListItem,
  VListItemTitle,
  VMenu,
  VProgressCircular,
} from "vuetify/components";

import { useInteractiveChoiceAttentionStore } from "@/stores/interactiveChoiceAttention";
import { useRunFinishedAttentionStore } from "@/stores/runFinishedAttention";
import { useSessionUnreadStore } from "@/stores/sessionUnread";
import type { ConfirmDialogHandler } from "@/utils/confirmDialog";
import ProjectList, { type Project } from "./ProjectList.vue";

const project: Project = {
  project_id: "p1",
  title: "Demo Project",
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

enableAutoUnmount(afterEach);
beforeEach(() => {
  vi.stubGlobal("devicePixelRatio", 1);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function mountList(eager = false, confirm?: ConfirmDialogHandler) {
  return mount(ProjectList, {
    attachTo: document.body,
    props: {
      projects: [project],
      projectSessions: {
        p1: [{ session_id: "sess-1", updated_at: "2026-09-01T00:00:00Z" }],
      },
      loadingProjectIds: [],
    },
    global: {
      provide: { $confirm: confirm },
      plugins: [
        createPinia(),
        createVuetify({
          components: {
            VBtn,
            VCard,
            VCheckboxBtn,
            VDivider,
            VList,
            VListItem,
            VListItemTitle,
            VMenu,
            VProgressCircular,
          },
          defaults: { VMenu: { eager } },
        }),
      ],
    },
  });
}

async function clickMenuItem(
  wrapper: ReturnType<typeof mountList>,
  label: string,
) {
  const item = wrapper
    .findAllComponents(VListItem)
    .find((item) => item.text() === label);
  expect(item, `Menu action: ${label}`).toBeDefined();
  await item!.trigger("click");
  await flushPromises();
}

describe("ProjectList ask_user_choice highlight", () => {
  beforeEach(() => {
    localStorage.removeItem("chat.projectExpandedIds");
    localStorage.setItem("chat.projectExpandedIds", JSON.stringify(["p1"]));
  });

  it("renders plain rows without highlight when no choice is pending", () => {
    const wrapper = mountList();
    const row = wrapper.find(".project-session-row");

    expect(row.exists()).toBe(true);
    expect(row.classes()).not.toContain("needs-choice");
    expect(wrapper.find(".project-session-choice-dot").exists()).toBe(false);
  });

  it.each([false, true])(
    "keeps session actions unavailable before a session is chosen (eager: %s)",
    async (eager) => {
      const wrapper = mountList(eager);
      await wrapper.vm.$nextTick();

      expect(wrapper.find(".project-session-row").exists()).toBe(true);
      expect(document.querySelector(".styled-menu-item")).toBeNull();
      expect(wrapper.emitted("exportSession")).toBeUndefined();
      expect(wrapper.emitted("editSessionTitle")).toBeUndefined();
      expect(wrapper.emitted("archiveSession")).toBeUndefined();
    },
  );

  it("highlights rows and shows the pulsing dot for pending sessions", async () => {
    const wrapper = mountList();
    const store = useInteractiveChoiceAttentionStore();
    store.add("sess-1");
    await wrapper.vm.$nextTick();

    const row = wrapper.find(".project-session-row");
    expect(row.classes()).toContain("needs-choice");
    expect(wrapper.find(".project-session-choice-dot").exists()).toBe(true);
  });

  it("drops the highlight reactively once the choice is resolved", async () => {
    const wrapper = mountList();
    const store = useInteractiveChoiceAttentionStore();
    store.add("sess-1");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".project-session-row").classes()).toContain(
      "needs-choice",
    );

    store.clear("sess-1");
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".project-session-row").classes()).not.toContain(
      "needs-choice",
    );
    expect(wrapper.find(".project-session-choice-dot").exists()).toBe(false);
  });

  it("shows the steady finished dot for runs that ended unseen", async () => {
    const wrapper = mountList();
    const finished = useRunFinishedAttentionStore();
    finished.add("sess-1");
    await wrapper.vm.$nextTick();

    const row = wrapper.find(".project-session-row");
    expect(row.classes()).toContain("has-finished-run");
    expect(row.classes()).not.toContain("needs-choice");
    expect(wrapper.find(".project-session-finished-dot").exists()).toBe(true);
    // The pending-choice dot stays hidden — the markers are exclusive.
    expect(wrapper.find(".project-session-choice-dot").exists()).toBe(false);
  });

  it("prefers the pending-choice marker when both states apply", async () => {
    const wrapper = mountList();
    useInteractiveChoiceAttentionStore().add("sess-1");
    useRunFinishedAttentionStore().add("sess-1");
    await wrapper.vm.$nextTick();

    const row = wrapper.find(".project-session-row");
    expect(row.classes()).toContain("needs-choice");
    expect(row.classes()).not.toContain("has-finished-run");
    expect(wrapper.find(".project-session-choice-dot").exists()).toBe(true);
    expect(wrapper.find(".project-session-finished-dot").exists()).toBe(false);
  });
});

describe("ProjectList session menu", () => {
  beforeEach(() => {
    localStorage.setItem("chat.projectExpandedIds", JSON.stringify(["p1"]));
  });

  it.each([
    {
      label: "编辑会话名称",
      event: "editSessionTitle",
      args: ["sess-2", "Second session"],
    },
    { label: "导出会话", event: "exportSession", args: ["sess-2"] },
    { label: "归档会话", event: "archiveSession", args: ["sess-2", "p2"] },
    {
      label: "取消星标",
      event: "toggleSessionStar",
      args: [
        {
          session_id: "sess-2",
          display_name: "Second session",
          updated_at: "2026-09-02T00:00:00Z",
          starred: true,
        },
      ],
    },
  ])(
    "binds $event to the newly chosen session and project",
    async ({ label, event, args }) => {
      const wrapper = mountList();
      await wrapper.setProps({
        projects: [
          project,
          { ...project, project_id: "p2", title: "Second project" },
        ],
        selectedProjectId: "p2",
        projectSessions: {
          p1: [{ session_id: "sess-1", updated_at: "2026-09-01T00:00:00Z" }],
          p2: [
            {
              session_id: "sess-2",
              display_name: "Second session",
              updated_at: "2026-09-02T00:00:00Z",
              starred: true,
            },
          ],
        },
      });
      const rows = wrapper.findAll(".project-session-row");
      await rows[0].trigger("contextmenu", { clientX: 30, clientY: 40 });
      await flushPromises();
      expect(document.querySelector(".v-overlay--active")).not.toBeNull();

      await rows[1].get('[title="更多操作"]').trigger("click");
      await clickMenuItem(wrapper, label);

      expect(wrapper.emitted(event)).toEqual([args]);
    },
  );

  it("closes without an action and exports the session chosen after reopening", async () => {
    const wrapper = mountList();
    await wrapper.setProps({
      projectSessions: {
        p1: [
          { session_id: "sess-1", updated_at: "2026-09-01T00:00:00Z" },
          { session_id: "sess-2", updated_at: "2026-09-02T00:00:00Z" },
        ],
      },
    });
    const rows = wrapper.findAll(".project-session-row");
    await rows[0].get('[title="更多操作"]').trigger("click");
    await flushPromises();
    expect(document.querySelector(".v-overlay--active")).not.toBeNull();

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(document.querySelector(".v-overlay--active")).toBeNull();
    expect(wrapper.emitted("exportSession")).toBeUndefined();
    expect(wrapper.emitted("editSessionTitle")).toBeUndefined();
    expect(wrapper.emitted("archiveSession")).toBeUndefined();

    await rows[1].trigger("contextmenu", { clientX: 50, clientY: 60 });
    await clickMenuItem(wrapper, "导出会话");
    expect(wrapper.emitted("exportSession")).toEqual([["sess-2"]]);
  });

  it("keeps unread actions on the chosen row and acknowledges its finished marker", async () => {
    const wrapper = mountList();
    await wrapper.setProps({
      projectSessions: {
        p1: [
          { session_id: "sess-1", updated_at: "2026-09-01T00:00:00Z" },
          { session_id: "sess-2", updated_at: "2026-09-02T00:00:00Z" },
        ],
      },
    });
    const rows = wrapper.findAll(".project-session-row");
    await rows[1].trigger("contextmenu", { clientX: 50, clientY: 60 });
    await clickMenuItem(wrapper, "标为未读");
    expect(rows[1].find(".project-session-finished-dot").exists()).toBe(true);
    expect(rows[0].find(".project-session-finished-dot").exists()).toBe(false);
    expect(useSessionUnreadStore().isUnread("sess-1")).toBe(false);

    useRunFinishedAttentionStore().add("sess-2");
    await rows[1].trigger("contextmenu", { clientX: 50, clientY: 60 });
    await clickMenuItem(wrapper, "标为已读");
    expect(rows[1].find(".project-session-finished-dot").exists()).toBe(false);
    expect(useSessionUnreadStore().isUnread("sess-2")).toBe(false);
    expect(useRunFinishedAttentionStore().hasFinished("sess-2")).toBe(false);
  });

  it("requires delete confirmation and retains its session while the menu changes", async () => {
    let resolveConfirmation!: (accepted: boolean) => void;
    const confirm = vi
      .fn<Parameters<ConfirmDialogHandler>, ReturnType<ConfirmDialogHandler>>()
      .mockResolvedValueOnce(false)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveConfirmation = resolve;
          }),
      );
    const wrapper = mountList(false, confirm);
    await wrapper.setProps({
      projectSessions: {
        p1: [
          { session_id: "sess-1", updated_at: "2026-09-01T00:00:00Z" },
          { session_id: "sess-2", updated_at: "2026-09-02T00:00:00Z" },
        ],
      },
    });
    const rows = wrapper.findAll(".project-session-row");
    await rows[0].trigger("contextmenu", { clientX: 30, clientY: 40 });
    await clickMenuItem(wrapper, "删除此对话");
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(wrapper.emitted("deleteSession")).toBeUndefined();

    await rows[0].trigger("contextmenu", { clientX: 30, clientY: 40 });
    await clickMenuItem(wrapper, "删除此对话");
    await rows[1].trigger("contextmenu", { clientX: 50, clientY: 60 });
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(wrapper.emitted("deleteSession")).toBeUndefined();
    resolveConfirmation(true);
    await flushPromises();
    expect(wrapper.emitted("deleteSession")).toEqual([["sess-1", "p1"]]);
  });
});

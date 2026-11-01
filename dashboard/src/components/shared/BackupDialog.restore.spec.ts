import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";
import BackupDialog from "./BackupDialog.vue";
import { backupApi } from "@/api/v1";
import { restartAstrBot } from "@/utils/restartAstrBot";
import { expireAccountSession } from "@/utils/accountSession";

vi.mock("@/api/v1", () => ({ backupApi: { list: vi.fn(), progress: vi.fn() }, statsApi: {}, authApi: {} }));
vi.mock("@/utils/restartAstrBot", () => ({ restartAstrBot: vi.fn().mockResolvedValue(undefined) }));
vi.mock("@/utils/accountSession", () => ({ expireAccountSession: vi.fn() }));
vi.mock("@/utils/confirmDialog", () => ({ askForConfirmation: vi.fn(), useConfirmDialog: () => ({}) }));
afterEach(() => { vi.clearAllMocks(); sessionStorage.clear(); document.body.innerHTML = ""; });

describe("full restore dialog recovery", () => {
  it("reopens the actual import receipt after login and routes its restart button through afterRestore", async () => {
    sessionStorage.setItem("astrbot-full-restore", "restore-a");
    vi.mocked(backupApi.list).mockResolvedValue({ data: { data: { items: [] } } } as any);
    vi.mocked(backupApi.progress).mockResolvedValue({ data: { status: "ok", data: { status: "completed", restart_required: true } } } as any);
    const wrapper = mount(BackupDialog, { attachTo: document.body, global: { plugins: [createVuetify({ components })], stubs: { WaitingForRestart: true } } });
    try {
      (wrapper.vm as any).open();
      await flushPromises();
      expect(backupApi.progress).toHaveBeenCalledWith("restore-a");
      const restart = Array.from(document.querySelectorAll("button")).find(button => /Restart Now|立即重启/.test(button.textContent || ""));
      expect(restart).toBeDefined();
      restart!.click();
      await flushPromises();
      expect(restartAstrBot).toHaveBeenCalledWith(expect.anything(), true);
    } finally { wrapper.unmount(); }
  });
  it("treats 401 as reauthentication, never as an import success", async () => {
    sessionStorage.setItem("astrbot-full-restore", "restore-b");
    vi.mocked(backupApi.list).mockResolvedValue({ data: { data: { items: [] } } } as any);
    vi.mocked(backupApi.progress).mockRejectedValue({ response: { status: 401 } });
    const wrapper = mount(BackupDialog, { attachTo: document.body, global: { plugins: [createVuetify({ components })], stubs: { WaitingForRestart: true } } });
    try {
      (wrapper.vm as any).open();
      await flushPromises();
      expect(expireAccountSession).toHaveBeenCalledTimes(1);
      expect(document.body.textContent).not.toMatch(/Import Completed|导入完成/);
      expect(restartAstrBot).not.toHaveBeenCalled();
      expect(sessionStorage.getItem("astrbot-full-restore")).toBe("restore-b");
    } finally { wrapper.unmount(); }
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";
import { VForm, VTextField } from "vuetify/components";
import { accountApi } from "@/api/v1";
import AccountsPage from "./AccountsPage.vue";

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

describe("ordinary account controls", () => {
  it("resets a selected password and confirms disabling without changing another account", async () => {
    const account = {
      account_id: "account-a",
      username: "alice",
      enabled: true,
      role: "user",
      resource_owner: "account-a",
    };
    const other = { ...account, account_id: "account-b", username: "bob" };
    vi.spyOn(accountApi, "list").mockImplementation(
      async () => ({ data: { data: [{ ...account }, other] } }) as any,
    );
    const reset = vi
      .spyOn(accountApi, "resetPassword")
      .mockResolvedValue({ data: { data: account } } as any);
    const disable = vi
      .spyOn(accountApi, "disable")
      .mockImplementation(async () => {
        account.enabled = false;
        return { data: { data: account } } as any;
      });
    const wrapper = mount(AccountsPage, {
      attachTo: document.body,
      global: { plugins: [createVuetify({ components })] },
    });
    try {
      await flushPromises();
      const firstRow = wrapper.findAll("tbody tr")[0];
      await firstRow.findAll("button")[0].trigger("click");
      await flushPromises();
      expect(document.body.textContent).toContain("现有登录凭证将失效");
      await wrapper
        .findComponent(VTextField)
        .find("input")
        .setValue("NewPassword123!");
      await wrapper.findComponent(VForm).trigger("submit");
      await flushPromises();
      expect(reset).toHaveBeenCalledTimes(1);
      expect(reset).toHaveBeenCalledWith("account-a", "NewPassword123!");
      expect(wrapper.text()).toContain("密码已重置");
      await firstRow.findAll("button")[1].trigger("click");
      await flushPromises();
      expect(disable).not.toHaveBeenCalled();
      expect(document.body.textContent).toContain("此用户将无法登录");
      const confirm = Array.from(
        document.querySelectorAll(".v-overlay button"),
      ).find((button) => button.textContent?.includes("禁用账户"));
      expect(confirm).toBeDefined();
      (confirm as HTMLButtonElement).click();
      await flushPromises();
      expect(disable).toHaveBeenCalledTimes(1);
      expect(disable).toHaveBeenCalledWith("account-a");
      expect(wrapper.findAll("tbody tr")[0].text()).toContain("已禁用");
      expect(wrapper.findAll("tbody tr")[1].text()).toContain("已启用");
      expect(
        wrapper.findAll("tbody tr")[0].findAll("button")[1].text(),
      ).toContain("启用账户");
    } finally {
      wrapper.unmount();
    }
  });

  it("keeps a rejected reset visible and does not report success", async () => {
    vi.spyOn(accountApi, "list").mockResolvedValue({
      data: { data: [{ account_id: "a", username: "alice", enabled: true }] },
    } as any);
    vi.spyOn(accountApi, "resetPassword").mockRejectedValue({
      response: { data: { message: "Password must include a number" } },
    });
    const wrapper = mount(AccountsPage, {
      attachTo: document.body,
      global: { plugins: [createVuetify({ components })] },
    });
    try {
      await flushPromises();
      await wrapper.find("tbody button").trigger("click");
      await flushPromises();
      await wrapper
        .findComponent(VTextField)
        .find("input")
        .setValue("WeakPassword");
      await wrapper.findComponent(VForm).trigger("submit");
      await flushPromises();
      expect(document.body.textContent).toContain(
        "Password must include a number",
      );
      expect(wrapper.text()).not.toContain("密码已重置");
    } finally {
      wrapper.unmount();
    }
  });

  it("enables a disabled account and requires confirmation before permanent deletion", async () => {
    const account = {
      account_id: "account-a",
      username: "alice",
      enabled: false,
      role: "user",
      resource_owner: "account-a",
    };
    const other = {
      ...account,
      account_id: "account-b",
      username: "bob",
      enabled: true,
    };
    let rows = [account, other];
    vi.spyOn(accountApi, "list").mockImplementation(
      async () => ({ data: { data: rows.map((row) => ({ ...row })) } }) as any,
    );
    const enable = vi
      .spyOn(accountApi, "enable")
      .mockImplementation(async () => {
        account.enabled = true;
        return { data: { data: account } } as any;
      });
    const remove = vi
      .spyOn(accountApi, "delete")
      .mockImplementation(async () => {
        rows = [other];
        return { data: { data: {} } } as any;
      });
    const wrapper = mount(AccountsPage, {
      attachTo: document.body,
      global: { plugins: [createVuetify({ components })] },
    });
    try {
      await flushPromises();
      expect(wrapper.text()).toContain("多用户管理");
      expect(wrapper.text()).toContain("单实例多用户");
      await wrapper
        .findAll("tbody tr")[0]
        .findAll("button")[1]
        .trigger("click");
      await flushPromises();
      expect(enable).toHaveBeenCalledWith("account-a");
      expect(wrapper.text()).toContain("旧登录凭证仍然失效");
      await wrapper
        .findAll("tbody tr")[0]
        .findAll("button")[2]
        .trigger("click");
      await flushPromises();
      expect(remove).not.toHaveBeenCalled();
      expect(document.body.textContent).toContain(
        "全部聊天记录、附件、项目、偏好",
      );
      const cancel = Array.from(
        document.querySelectorAll(".v-overlay button"),
      ).find((button) => button.textContent?.includes("取消"));
      (cancel as HTMLButtonElement).click();
      await flushPromises();
      expect(remove).not.toHaveBeenCalled();
      await wrapper
        .findAll("tbody tr")[0]
        .findAll("button")[2]
        .trigger("click");
      await flushPromises();
      const confirm = Array.from(
        document.querySelectorAll(".v-overlay button"),
      ).find((button) => button.textContent?.includes("删除账户"));
      (confirm as HTMLButtonElement).click();
      await flushPromises();
      expect(remove).toHaveBeenCalledTimes(1);
      expect(remove).toHaveBeenCalledWith("account-a");
      expect(wrapper.findAll("tbody tr")).toHaveLength(1);
      expect(wrapper.find("tbody tr").text()).toContain("bob");
      expect(wrapper.text()).toContain("已永久删除");
    } finally {
      wrapper.unmount();
    }
  });

  it("keeps cleanup failure and retry confirmation visible without a success notice", async () => {
    const account = { account_id: "a", username: "alice", enabled: true };
    vi.spyOn(accountApi, "list").mockImplementation(
      async () => ({ data: { data: [{ ...account }] } }) as any,
    );
    vi.spyOn(accountApi, "delete").mockImplementation(async () => {
      account.enabled = false;
      throw {
        response: {
          data: { message: "Account cleanup failed. Retry deletion." },
        },
      };
    });
    const wrapper = mount(AccountsPage, {
      attachTo: document.body,
      global: { plugins: [createVuetify({ components })] },
    });
    try {
      await flushPromises();
      await wrapper
        .findAll("tbody tr")[0]
        .findAll("button")[2]
        .trigger("click");
      await flushPromises();
      const confirm = Array.from(
        document.querySelectorAll(".v-overlay button"),
      ).find((button) => button.textContent?.includes("删除账户"));
      (confirm as HTMLButtonElement).click();
      await flushPromises();
      expect(document.body.textContent).toContain(
        "Account cleanup failed. Retry deletion.",
      );
      expect(wrapper.find("tbody tr").text()).toContain("已禁用");
      expect(wrapper.text()).not.toContain("已永久删除");
    } finally {
      wrapper.unmount();
    }
  });
});

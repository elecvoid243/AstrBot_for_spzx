import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parse } from "vue/compiler-sfc";
import ts from "typescript";
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ImportSessionsDialog from "./ImportSessionsDialog.vue";
import { chatApi } from "@/api/v1";
import {
  accountSessionRevision,
  assertCurrentAccount,
  resetAccountSession,
} from "@/utils/accountSession";

vi.mock("@/api/v1", () => ({
  chatApi: {
    importSessions: vi.fn(),
    confirmImportSessions: vi.fn(),
    exportSession: vi.fn(),
  },
}));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

function mountDialog() {
  const passthrough = { template: "<div><slot /></div>" };
  return mount(ImportSessionsDialog, {
    props: { modelValue: true },
    global: {
      stubs: {
        VDialog: passthrough,
        VCard: passthrough,
        VCardTitle: passthrough,
        VCardText: passthrough,
        VCardActions: passthrough,
        VList: passthrough,
        VListItem: passthrough,
        VListItemTitle: passthrough,
        VListItemSubtitle: passthrough,
        VAlert: passthrough,
        VSpacer: true,
        VFileInput: {
          template: '<input type="file" @change="$emit(\'change\', $event)" />',
        },
        VBtn: {
          template: "<button @click=\"$emit('click')\"><slot /></button>",
        },
      },
    },
  });
}

async function upload(wrapper: ReturnType<typeof mountDialog>) {
  const input = wrapper.find("input");
  Object.defineProperty(input.element, "files", {
    value: [new File(["archive"], "private.zip", { type: "application/zip" })],
  });
  await input.trigger("change");
  await flushPromises();
}

beforeEach(() => {
  resetAccountSession();
  vi.clearAllMocks();
});

describe("account-owned archive UI", () => {
  it("uploads, previews and opens the confirmed sessions", async () => {
    vi.mocked(chatApi.importSessions).mockResolvedValue({
      data: {
        data: {
          import_id: "own-upload",
          can_import: true,
          sessions: [{ display_name: "My session", stats: {} }],
        },
      },
    } as any);
    vi.mocked(chatApi.confirmImportSessions).mockResolvedValue({
      data: {
        data: {
          created: [{ new_session_id: "restored" }],
          warnings: [],
          errors: [],
        },
      },
    } as any);
    const wrapper = mountDialog();
    await upload(wrapper);
    expect(wrapper.text()).toContain("My session");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "import.confirm")!
      .trigger("click");
    await flushPromises();
    expect(chatApi.confirmImportSessions).toHaveBeenCalledWith("own-upload");
    expect(wrapper.emitted("imported")).toEqual([[["restored"]]]);
    wrapper.unmount();
  });

  it.each(["upload", "confirm"])(
    "discards a delayed %s response after account switch",
    async (stage) => {
      let complete!: (value: any) => void;
      vi.mocked(chatApi.importSessions).mockImplementation(
        () =>
          new Promise((resolve) => {
            complete = resolve;
          }),
      );
      const wrapper = mountDialog();
      await upload(wrapper);
      if (stage === "confirm") {
        complete({
          data: {
            data: { import_id: "old-id", can_import: true, sessions: [] },
          },
        });
        await flushPromises();
        vi.mocked(chatApi.confirmImportSessions).mockImplementation(
          () =>
            new Promise((resolve) => {
              complete = resolve;
            }),
        );
        await wrapper
          .findAll("button")
          .find((button) => button.text() === "import.confirm")!
          .trigger("click");
      }
      resetAccountSession();
      complete({
        data: {
          data: {
            import_id: "old-id",
            can_import: true,
            sessions: [{ display_name: "OLD PRIVATE SESSION", stats: {} }],
            created: [{ new_session_id: "old-private-id" }],
          },
        },
      });
      await flushPromises();
      expect(wrapper.text()).not.toContain("OLD PRIVATE SESSION");
      expect(wrapper.emitted("imported")).toBeUndefined();
      expect(wrapper.emitted("update:modelValue")).toContainEqual([false]);
      wrapper.unmount();
    },
  );

  it.each(["account switch", "empty response"])(
    "does not download after %s",
    async (reason) => {
      const script = parse(
        readFileSync(resolve("src/components/chat/Chat.vue"), "utf8"),
      ).descriptor.scriptSetup!.content;
      const ast = ts.createSourceFile(
        "Chat.ts",
        script,
        ts.ScriptTarget.Latest,
        true,
      );
      const code = ast.statements
        .filter(
          (node) =>
            ts.isFunctionDeclaration(node) &&
            ["exportSidebarSession", "exportErrorMessage"].includes(
              node.name!.text,
            ),
        )
        .map(
          (node) =>
            ts.transpileModule(node.getText(ast), {
              compilerOptions: { target: ts.ScriptTarget.ES2022 },
            }).outputText,
        )
        .join("\n");
      const toast = { success: vi.fn(), error: vi.fn() };
      const action = new Function(
        "chatApi",
        "accountSessionRevision",
        "chatAccountRevision",
        "assertCurrentAccount",
        "toast",
        "tm",
        `${code}; return exportSidebarSession;`,
      )(
        chatApi,
        accountSessionRevision,
        accountSessionRevision.value,
        assertCurrentAccount,
        toast,
        (key: string) => key,
      );
      let complete!: (value: any) => void;
      vi.mocked(chatApi.exportSession).mockImplementation(
        () =>
          new Promise((resolve) => {
            complete = resolve;
          }),
      );
      const click = vi
        .spyOn(HTMLAnchorElement.prototype, "click")
        .mockImplementation(() => {});
      const pending = action("private-session");
      const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});
      if (reason === "account switch") resetAccountSession();
      complete({
        data: new Blob(reason === "empty response" ? [] : ["PRIVATE ZIP"]),
      });
      await pending;
      expect(click).not.toHaveBeenCalled();
      expect(toast.success).not.toHaveBeenCalled();
      if (reason === "empty response")
        expect(toast.error).toHaveBeenCalledWith("conversation.exportFailed");
      else expect(toast.error).not.toHaveBeenCalled();
      click.mockRestore();
      errorLog.mockRestore();
    },
  );
});

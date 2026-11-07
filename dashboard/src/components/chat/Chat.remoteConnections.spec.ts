import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { parse } from "vue/compiler-sfc";
import { expect, it, vi } from "vitest";
import { ref } from "vue";

const script = parse(
  readFileSync(resolve("src/components/chat/Chat.vue"), "utf8"),
).descriptor.scriptSetup!.content;
const ast = ts.createSourceFile(
  "Chat.ts",
  script,
  ts.ScriptTarget.Latest,
  true,
);
const declaration = ast.statements.find(
  (node) =>
    ts.isFunctionDeclaration(node) && node.name?.text === "selectRemoteSession",
)!;
const source = ts.transpile(declaration.getText(ast), {
  target: ts.ScriptTarget.ESNext,
});
function harness(allow: boolean) {
  const confirmFileNavigation = vi.fn().mockResolvedValue(allow);
  const closeSecondaryPanels = vi.fn(),
    closeTodoMenu = vi.fn(),
    closeMobileSidebar = vi.fn();
  const gitDiffSidebarOpen = ref(true),
    fileReferenceNavigation = ref({ path: "C:/local/draft.txt" }),
    selectedProjectId = ref("local-project"),
    currSessionId = ref("local-session");
  const chatHeader = { SET_GOAL_SIDEBAR_OPEN: vi.fn() },
    router = { push: vi.fn().mockResolvedValue(undefined) };
  const run = new Function(
    "confirmFileNavigation",
    "closeSecondaryPanels",
    "gitDiffSidebarOpen",
    "fileReferenceNavigation",
    "closeTodoMenu",
    "chatHeader",
    "selectedProjectId",
    "currSessionId",
    "router",
    "basePath",
    "closeMobileSidebar",
    `${source}; return selectRemoteSession;`,
  )(
    confirmFileNavigation,
    closeSecondaryPanels,
    gitDiffSidebarOpen,
    fileReferenceNavigation,
    closeTodoMenu,
    chatHeader,
    selectedProjectId,
    currSessionId,
    router,
    () => "/chat",
    closeMobileSidebar,
  );
  return {
    run,
    confirmFileNavigation,
    closeSecondaryPanels,
    gitDiffSidebarOpen,
    fileReferenceNavigation,
    selectedProjectId,
    currSessionId,
    router,
  };
}
it("preserves the local conversation and editor when leaving is cancelled", async () => {
  const state = harness(false);
  await state.run("remote-a", "same-id");
  expect(state.confirmFileNavigation).toHaveBeenCalledOnce();
  expect(state.gitDiffSidebarOpen.value).toBe(true);
  expect(state.fileReferenceNavigation.value?.path).toBe("C:/local/draft.txt");
  expect(state.currSessionId.value).toBe("local-session");
  expect(state.selectedProjectId.value).toBe("local-project");
  expect(state.closeSecondaryPanels).not.toHaveBeenCalled();
  expect(state.router.push).not.toHaveBeenCalled();
});
it("closes local operation panels after approval before selecting a remote target", async () => {
  const state = harness(true);
  await state.run("remote-a", "same-id");
  expect(state.closeSecondaryPanels).toHaveBeenCalledOnce();
  expect(state.gitDiffSidebarOpen.value).toBe(false);
  expect(state.fileReferenceNavigation.value).toBeNull();
  expect(state.currSessionId.value).toBe("");
  expect(state.router.push).toHaveBeenCalledWith({
    path: "/chat",
    query: { remoteConnection: "remote-a", remoteSession: "same-id" },
  });
});

// Chat.vue project-creation contract (2026-11-07).
//
// The sidebar "+" must create a project, not open the spcode directory-load
// dialog. After creation Chat must navigate to the new project page so the
// user creates its first session there; the conversation that happened to be
// open must not be attached to the new project.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { parse } from "vue/compiler-sfc";
import { describe, expect, it, vi } from "vitest";
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
const names = ["openCreateProjectDialog", "saveProject"];
const functions = ast.statements.filter(
  (node) =>
    ts.isFunctionDeclaration(node) &&
    node.name !== undefined &&
    names.includes(node.name.text),
);
if (functions.length !== names.length) {
  throw new Error(`Chat.vue must define ${names.join(" and ")}`);
}
const code = ts.transpileModule(
  functions.map((node) => node.getText(ast)).join("\n"),
  { compilerOptions: { target: ts.ScriptTarget.ES2022 } },
).outputText;

const formData = {
  title: "New project",
  emoji: "📁",
  description: "",
  workspace_type: "custom" as const,
  workspace_path: "C:/projects/new",
};

function setup() {
  const state = {
    projectDialogOpen: ref(false),
    editingProject: ref<{ project_id: string } | null>(null),
    projectDialogError: ref(""),
    savingProject: ref(false),
    createProject: vi.fn().mockResolvedValue({ project_id: "p-new" }),
    updateProject: vi.fn().mockResolvedValue(undefined),
    selectProject: vi.fn().mockResolvedValue(undefined),
    inputRef: ref({ openLoadDialog: vi.fn() }),
  };
  const actions = new Function(
    ...Object.keys(state),
    `${code}; return { openCreateProjectDialog, saveProject };`,
  )(...Object.values(state)) as {
    openCreateProjectDialog: () => Promise<void>;
    saveProject: (form: typeof formData, projectId?: string) => Promise<void>;
  };
  return { state, actions };
}

describe("Chat project creation", () => {
  it("opens the project dialog from the sidebar + without loading a directory", async () => {
    const { state, actions } = setup();

    await actions.openCreateProjectDialog();

    expect(state.projectDialogOpen.value).toBe(true);
    expect(state.editingProject.value).toBeNull();
    expect(state.inputRef.value.openLoadDialog).not.toHaveBeenCalled();
  });

  it("navigates to the created project instead of attaching the open conversation", async () => {
    const { state, actions } = setup();
    state.projectDialogOpen.value = true;

    await actions.saveProject(formData);

    expect(state.createProject).toHaveBeenCalledOnce();
    expect(state.selectProject).toHaveBeenCalledWith("p-new");
    expect(state.projectDialogOpen.value).toBe(false);
    expect(state.editingProject.value).toBeNull();
  });

  it("keeps editing an existing project in place", async () => {
    const { state, actions } = setup();
    state.projectDialogOpen.value = true;
    state.editingProject.value = { project_id: "p-1" };

    await actions.saveProject(formData, "p-1");

    expect(state.updateProject).toHaveBeenCalledOnce();
    expect(state.createProject).not.toHaveBeenCalled();
    expect(state.selectProject).not.toHaveBeenCalled();
    expect(state.projectDialogOpen.value).toBe(false);
  });

  it("surfaces creation errors without navigating", async () => {
    const { state, actions } = setup();
    state.projectDialogOpen.value = true;
    state.createProject.mockRejectedValueOnce(new Error("boom"));

    await actions.saveProject(formData);

    expect(state.projectDialogError.value).toBe("boom");
    expect(state.selectProject).not.toHaveBeenCalled();
    expect(state.projectDialogOpen.value).toBe(true);
  });
});

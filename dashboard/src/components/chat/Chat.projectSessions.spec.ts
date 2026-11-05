import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { parse } from "vue/compiler-sfc";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { effectScope, nextTick, ref } from "vue";
import {
  accountSessionRevision,
  assertCurrentAccount,
} from "@/utils/accountSession";

vi.mock("@/api/v1", () => ({
  chatApi: {
    sendStreamUrl: () => "/api/v1/chat",
    systemStreamUrl: (id: string) =>
      `/api/v1/chat/sessions/${id}/system-stream`,
    getMarkers: vi.fn().mockResolvedValue({ data: { data: { markers: [] } } }),
  },
  fileApi: { contentUrl: () => "" },
}));
vi.mock("@/api/http", () => ({ fetchWithAuth: vi.fn() }));

import { fetchWithAuth } from "@/api/http";
import { useMessages } from "@/composables/useMessages";

// Exercise Chat.vue's actual cache writes and callback together with the real
// stream lifecycle; avoid mounting unrelated editor, browser and shell panels.
const script = parse(
  readFileSync(resolve("src/components/chat/Chat.vue"), "utf8"),
).descriptor.scriptSetup!.content;
const ast = ts.createSourceFile(
  "Chat.ts",
  script,
  ts.ScriptTarget.Latest,
  true,
);
const functions = ast.statements.filter(
  (node) =>
    ts.isFunctionDeclaration(node) &&
    ["loadProjectSessions", "handleProjectToggle"].includes(node.name!.text),
);
const call = ast.statements
  .filter(ts.isVariableStatement)
  .flatMap((node) => [...node.declarationList.declarations])
  .map((node) => node.initializer)
  .find(
    (node) =>
      node &&
      ts.isCallExpression(node) &&
      node.expression.getText(ast) === "useMessages",
  );
if (
  !call ||
  !ts.isCallExpression(call) ||
  !ts.isObjectLiteralExpression(call.arguments[0])
) {
  throw new Error("Chat.vue must provide useMessages options");
}
const callback = call.arguments[0].properties.find(
  (node) =>
    ts.isPropertyAssignment(node) &&
    node.name.getText(ast) === "onSessionsChanged",
);
if (!callback || !ts.isPropertyAssignment(callback)) {
  throw new Error("Chat.vue must provide its session-list refresh callback");
}
const code = ts.transpileModule(
  `${functions.map((node) => node.getText(ast)).join("\n")}
   const onSessionsChanged = ${callback.initializer.getText(ast)};`,
  { compilerOptions: { target: ts.ScriptTarget.ES2022 } },
).outputText;

beforeEach(() => {
  // Match the browser brand: happy-dom otherwise lets Vue proxy controllers,
  // breaking the stream's identity check before it invokes the refresh hook.
  vi.stubGlobal(
    "AbortController",
    class extends AbortController {
      readonly [Symbol.toStringTag] = "AbortController";
    },
  );
});
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("Agent creation refreshes cached project conversations after switching away", () => {
  it.each([null, "q"])(
    "updates the cached source project while viewing %s",
    async (currentProject) => {
      setActivePinia(createPinia());
      const scope = effectScope();
      const source = { session_id: "A", display_name: "Source A" };
      const current = { session_id: "C", display_name: "Current C" };
      const created = { session_id: "B", display_name: "Created B" };
      const serverProjects: Record<string, (typeof source)[]> = {
        p: [source],
        q: [current],
      };
      const state = {
        selectedProjectId: ref<string | null>("p"),
        projectSessions: ref([source]),
        projectSessionsById: ref({ p: [source], q: [current] }),
        loadingProjectSessionIds: ref<string[]>([]),
        chatAccountRevision: accountSessionRevision.value,
        assertCurrentAccount,
        getSessions: vi.fn().mockResolvedValue(undefined),
        getProjectSessions: vi.fn(async (id: string) => [
          ...serverProjects[id],
        ]),
      };
      const actions = new Function(
        ...Object.keys(state),
        `${code}; return { onSessionsChanged, handleProjectToggle };`,
      )(...Object.values(state));
      const currentSessionId = ref("A");
      const onStreamEnd = vi.fn();
      let controller!: ReadableStreamDefaultController<Uint8Array>;
      let sourceSystemSignal: AbortSignal | undefined;
      vi.mocked(fetchWithAuth).mockImplementation(async (url, options) => {
        if (String(url).includes("system-stream")) {
          if (String(url).includes("/A/"))
            sourceSystemSignal = options?.signal ?? undefined;
          return new Response(null, { status: 403 });
        }
        return new Response(
          new ReadableStream({
            start(value) {
              controller = value;
            },
          }),
          {
            headers: { "content-type": "text/event-stream" },
          },
        );
      });
      const messages = scope.run(() =>
        useMessages({
          currentSessionId,
          onSessionsChanged: actions.onSessionsChanged,
          onStreamEnd,
        }),
      )!;
      try {
        const parts = [
          { type: "plain", text: "Create a project conversation" },
        ];
        const exchange = messages.createLocalExchange({
          sessionId: "A",
          messageId: "a-run",
          parts,
        });
        messages.sendMessageStream({
          sessionId: "A",
          messageId: "a-run",
          parts,
          transport: "sse",
          ...exchange,
        });
        await vi.waitFor(() => expect(controller).toBeDefined());
        currentSessionId.value = "C";
        state.selectedProjectId.value = currentProject;
        state.projectSessions.value = currentProject ? [current] : [];
        await nextTick();
        expect(sourceSystemSignal?.aborted).toBe(true);
        // A creates B after its source system stream was closed, so the page
        // receives no project-specific creation notice. The run still completes.
        serverProjects.p.push(created);
        const frames = [{ type: "plain", data: "B created" }, { type: "end" }]
          .map((payload) => `data: ${JSON.stringify(payload)}\n\n`)
          .join("");
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
        await vi.waitFor(() => expect(onStreamEnd).toHaveBeenCalledWith("A"));
        expect(state.projectSessionsById.value.p).toEqual([source, created]);
        expect(state.projectSessions.value).toEqual(
          currentProject ? [current] : [],
        );
        await actions.handleProjectToggle("p", false);
        await actions.handleProjectToggle("p", true);
        expect(
          state.projectSessionsById.value.p.map((row) => row.display_name),
        ).toEqual(["Source A", "Created B"]);
      } finally {
        messages.cleanupConnections();
        scope.stop();
      }
    },
  );
});

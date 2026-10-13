import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parse } from "vue/compiler-sfc";
import ts from "typescript";
import { ref } from "vue";
import { describe, expect, it } from "vitest";

// Exercise Chat's actual index projection, including loaded-window fallbacks.
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
    ts.isFunctionDeclaration(node) && node.name?.text === "updateScrollMarkers",
)!;
const code = ts.transpileModule(declaration.getText(ast), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;

function projectMarkers(indexed = true) {
  const container = document.createElement("div");
  Object.defineProperties(container, {
    clientHeight: { value: 300 },
    scrollHeight: { value: 1000 },
    offsetWidth: { value: 600 },
    clientWidth: { value: 590 },
    scrollTop: { value: 100 },
  });
  container.getBoundingClientRect = () =>
    Object.assign(new DOMRect(0, 0, 590, 300), { top: 0 });
  const rows = Array.from({ length: 2 }, (_, i) => {
    const row = document.createElement("div");
    row.className = "message-row";
    row.getBoundingClientRect = () =>
      Object.assign(new DOMRect(0, i * 100, 500, 100), { top: i * 100 });
    container.append(row);
    return row;
  });
  const context = {
    messagesContainer: ref(container),
    stripHeight: ref(0),
    stripRightOffset: ref(0),
    scrollMarkers: ref<any[]>([]),
    currSessionId: ref("s1"),
    branchHistoryCollapsed: ref(false),
    historyOffset: ref(100),
    historyOffsetBySession: { s1: 100 },
    sessionMarkersBySession: {
      s1: {
        loaded: indexed,
        totalMessages: 102,
        markers: [
          {
            id: 1,
            index: 100,
            snippet: "Saved question",
            replySnippet: "Saved answer",
            inherited: false,
          },
        ],
      },
    },
    activeMessages: ref([
      { id: 1, content: { type: "user" } },
      { id: 2, content: { type: "bot" } },
    ]),
    activeMessagePreviews: ref(
      new Map([
        [
          "1",
          {
            preview: "Loaded question",
            replyPreview: "Live answer",
            replyLoading: true,
            messageIndex: 100,
          },
        ],
      ]),
    ),
    isUserMessage: (m: any) => m.content.type === "user",
    plainTextFromMessage: () => "Loaded question",
    truncate: (s: string) => s,
  };
  const update = new Function(
    ...Object.keys(context),
    `${code}; updateScrollMarkers();`,
  );
  return { context, rows, update: () => update(...Object.values(context)) };
}

describe("chat marker index projection", () => {
  it("replaces saved previews with live loaded reply text", () => {
    const { context, update } = projectMarkers();
    update();
    expect(context.scrollMarkers.value[0]).toMatchObject({
      id: 1,
      preview: "Loaded question",
      replyPreview: "Live answer",
      replyLoading: true,
      index: 100,
    });
  });
  it("retains server previews for questions outside the loaded history window", () => {
    const { context, update } = projectMarkers();
    context.sessionMarkersBySession.s1.markers[0].id = 99;
    context.sessionMarkersBySession.s1.markers[0].index = 20;
    update();
    expect(context.scrollMarkers.value.map((m) => m.id)).toEqual([99, 1]);
    expect(context.scrollMarkers.value[0].replyPreview).toBe("Saved answer");
    expect(context.scrollMarkers.value[1].replyPreview).toBe("Live answer");
  });
  it("adds loaded turns beyond the capped index and still honors inherited-history collapse", () => {
    const { context, rows, update } = projectMarkers();
    context.sessionMarkersBySession.s1.markers[0].id = 99;
    context.sessionMarkersBySession.s1.markers[0].index = 120;
    rows[0].classList.add("inherited-row");
    update();
    expect(context.scrollMarkers.value.map((m) => m.id)).toEqual([1, 99]);
    context.branchHistoryCollapsed.value = true;
    update();
    expect(context.scrollMarkers.value.map((m) => m.id)).toEqual([99]);
  });
  it("keeps reply previews in the DOM-measured fallback without replacing id-based jumps", () => {
    const { context, update } = projectMarkers(false);
    update();
    expect(context.scrollMarkers.value[0]).toMatchObject({
      id: 1,
      index: -1,
      messageIndex: 100,
      replyPreview: "Live answer",
      replyLoading: true,
    });
  });
});

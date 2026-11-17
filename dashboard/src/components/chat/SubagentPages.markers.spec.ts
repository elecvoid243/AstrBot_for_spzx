import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parse } from "vue/compiler-sfc";
import ts from "typescript";
import { ref } from "vue";
import { describe, expect, it } from "vitest";

// Exercise the subagent page's own marker projection against rendered rows.
const sfc = parse(
  readFileSync(resolve("src/components/chat/SubagentPages.vue"), "utf8"),
);
const ast = ts.createSourceFile(
  "SubagentPages.ts",
  sfc.descriptor.scriptSetup!.content,
  ts.ScriptTarget.Latest,
  true,
);
const grab = (name: string) => {
  const node = ast.statements.find(
    (statement) =>
      ts.isFunctionDeclaration(statement) && statement.name?.text === name,
  );
  if (!node) throw new Error(`missing ${name}`);
  return ts.transpileModule(node.getText(ast), {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
};
const code = `${grab("markerPreview")}\n${grab("updateScrollMarkers")}`;

const userRecord = (id: string, text: string) => ({
  id,
  content: { type: "user", message: [{ type: "plain", text }] },
});
const botRecord = (id: string, text: string) => ({
  id,
  content: { type: "bot", message: [{ type: "plain", text }] },
});

function projectMarkers() {
  const container = document.createElement("div");
  let scrollHeight = 1000;
  Object.defineProperties(container, {
    clientHeight: { value: 300 },
    scrollHeight: { get: () => scrollHeight },
    offsetWidth: { value: 600 },
    clientWidth: { value: 590 },
    scrollTop: { value: 0 },
  });
  container.getBoundingClientRect = () =>
    Object.assign(new DOMRect(0, 0, 590, 300), { top: 0 });
  const rows = new Map<string, HTMLElement>();
  for (const [id, top] of [
    ["run-1-task", 0],
    ["run-1-reply-1", 100],
    ["run-1-user-1", 500],
  ] as const) {
    const row = document.createElement("div");
    row.className = "message-row";
    row.dataset.messageId = id;
    row.getBoundingClientRect = () =>
      Object.assign(new DOMRect(0, top, 500, 100), { top });
    container.append(row);
    rows.set(id, row);
  }
  const context = {
    panel: ref<HTMLElement | null>(container),
    scrollMarkers: ref<any[]>([]),
    stripHeight: ref(0),
    stripRightOffset: ref(0),
    messages: ref([
      userRecord("run-1-task", "Delegated   task"),
      botRecord("run-1-reply-1", "Answer text"),
      userRecord("run-1-user-1", "Follow up"),
    ]),
    tm: (key: string) => key,
  };
  const update = new Function(
    ...Object.keys(context),
    `${code}; updateScrollMarkers();`,
  );
  return {
    context,
    container,
    rows,
    setScrollHeight: (value: number) => {
      scrollHeight = value;
    },
    update: () => update(...Object.values(context)),
  };
}

describe("subagent page marker rail", () => {
  it("projects only this page's user rows, positioned by measured offset", () => {
    const { context, update } = projectMarkers();
    update();
    expect(context.stripHeight.value).toBe(300);
    expect(context.stripRightOffset.value).toBe(10);
    expect(context.scrollMarkers.value.map((m) => m.id)).toEqual([
      "run-1-task",
      "run-1-user-1",
    ]);
    expect(context.scrollMarkers.value[0]).toMatchObject({
      ordinal: 1,
      preview: "Delegated task",
      replyPreview: "Answer text",
      topPct: 0,
    });
    expect(context.scrollMarkers.value[1]).toMatchObject({
      ordinal: 2,
      messageIndex: 2,
      preview: "Follow up",
      replyPreview: "",
      topPct: (500 / 700) * 100,
    });
  });

  it("drops rows that are not mounted instead of inventing positions", () => {
    const { context, rows, update } = projectMarkers();
    rows.get("run-1-user-1")!.remove();
    update();
    expect(context.scrollMarkers.value.map((m) => m.id)).toEqual([
      "run-1-task",
    ]);
    expect(context.scrollMarkers.value[0].ordinal).toBe(1);
  });

  it("clears the rail when the page cannot scroll or the panel is gone", () => {
    const { context, setScrollHeight, update } = projectMarkers();
    update();
    expect(context.scrollMarkers.value).toHaveLength(2);
    setScrollHeight(300);
    update();
    expect(context.scrollMarkers.value).toEqual([]);
    context.panel.value = null;
    update();
    expect(context.stripHeight.value).toBe(0);
    expect(context.stripRightOffset.value).toBe(0);
  });
});

describe("main conversation rail ownership", () => {
  it("is unmounted while a subagent page is open", () => {
    const chat = parse(
      readFileSync(resolve("src/components/chat/Chat.vue"), "utf8"),
    );
    expect(chat.descriptor.template!.content).toContain(
      'v-if="!subagentPageOpen && scrollMarkers.length"',
    );
  });
});

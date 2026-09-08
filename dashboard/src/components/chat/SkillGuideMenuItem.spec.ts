// SkillGuideMenuItem.spec.ts
// Author: elecvoid243 @ 2026-08-16
//
// Unit tests for the "手动加载 Skill" entry inside the chat input's "+"
// menu (astrbot_plugin_skill_guide companion UI).
//
// The skill list is a COMPLETELY SEPARATE v-menu (content teleported to
// <body>, positioned next to the item via location="end") — it never
// participates in the "+" menu's DOM/layout. The card is a plain div the
// tests bind directly to. Open/close is self-managed: hover opens the
// menu and it STAYS open when the pointer leaves — dismissal is explicit
// only (outside click / Escape / re-click on the activator / the card's
// corner close button). Un-selecting one skill keeps the others.
//
// Show-all mode (toggle off by default): flipping the switch lists every
// skill from the core GET /skills API (skillApi.list); skills the persona
// does not mount render gray (--unmounted) but queue the same way.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { flushPromises, mount } from "@vue/test-utils";
import { computed, defineComponent } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: vi.fn(), post: vi.fn() },
  skillApi: { list: vi.fn() },
}));

// Self-contained i18n mock (returns the key + k=v params), same shape
// as GitRepoInitPrompt.spec.ts — no dependency on the real tables.
const tmMock = vi.fn(
  (key: string, params?: Record<string, string | number>) => {
    if (!params) return key;
    return Object.entries(params).reduce(
      (acc, [k, v]) => `${acc} ${k}=${String(v)}`,
      key,
    );
  },
);
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: tmMock, getRaw: vi.fn() }),
}));

import { pluginExtensionApi, skillApi } from "@/api/v1";
import { useSkillGuide } from "@/composables/useSkillGuide";
import SkillGuideMenuItem from "./SkillGuideMenuItem.vue";

const getMock = pluginExtensionApi.get as ReturnType<typeof vi.fn>;
const postMock = pluginExtensionApi.post as ReturnType<typeof vi.fn>;
const listMock = skillApi.list as ReturnType<typeof vi.fn>;

const UMO = "webchat:FriendMessage:webchat!alice!sess-1";

const ACTIVE_PAYLOAD = {
  status: "ok",
  data: {
    persona: { id: "p1", name: "Default" },
    skills: [
      {
        name: "brainstorming",
        description: "Explore intent before implementation",
        path: "skills/brainstorming",
        source_type: "builtin",
      },
      {
        name: "pdf",
        description: "Work with PDF files",
        path: "skills/pdf",
        source_type: "builtin",
      },
    ],
  },
};

const okLoad = (skillName: string) => ({
  data: { status: "ok", data: { skill_name: skillName, queued: true } },
});
const okClear = (cleared: string[]) => ({
  data: { status: "ok", data: { cleared } },
});

// Core GET /skills payload: every skill in data/skills. "grilling" is on
// disk but NOT persona-mounted (missing from ACTIVE_PAYLOAD above).
const ALL_SKILLS_PAYLOAD = {
  status: "ok",
  data: {
    data: {
      runtime: "local",
      skills: [
        {
          name: "brainstorming",
          description: "Explore intent before implementation",
          path: "skills/brainstorming/SKILL.md",
          source_type: "local",
          active: true,
        },
        {
          name: "pdf",
          description: "Work with PDF files",
          path: "skills/pdf/SKILL.md",
          source_type: "local",
          active: true,
        },
        {
          name: "grilling",
          description: "Deep-dive questioning",
          path: "skills/grilling/SKILL.md",
          source_type: "local",
          active: true,
        },
      ],
    },
  },
};

// v-menu stub: driven by v-model (the component owns `open`), content
// renders only while open. The activator forwards clicks so "open on
// click" keeps working (hover is handled by the component itself).
const menuStub = defineComponent({
  props: { modelValue: { type: Boolean, default: false } },
  emits: ["update:modelValue"],
  setup(props) {
    const open = computed(() => props.modelValue);
    return { open };
  },
  template: `<div class="v-menu-stub"><slot name="activator" :props="{ onClick: () => $emit('update:modelValue', true) }" /><slot v-if="open" /></div>`,
});

// The card and skill rows are plain elements (not Vuetify components), so
// only the activator v-list-item and the show-all v-switch need stubs.
const stubs = {
  "v-menu": menuStub,
  "v-icon": { template: "<i><slot /></i>" },
  "v-list-item": {
    props: ["disabled"],
    emits: ["click"],
    template: `<div v-bind="$attrs" class="v-list-item-stub" :disabled="disabled" @click="$emit('click')"><slot name="prepend" /><slot /></div>`,
  },
  "v-list-item-title": { template: "<span><slot /></span>" },
  "v-switch": {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template: `<div v-bind="$attrs" class="v-switch-stub" @click="$emit('update:modelValue', !modelValue)"></div>`,
  },
};

function mountItem(props: Record<string, unknown> = {}) {
  return mount(SkillGuideMenuItem, {
    props: { sessionId: "sess-1", ...props },
    global: { stubs },
  });
}

/** Prime the singleton with a successful /skill-guide/active fetch. */
async function primeSession(payload: unknown = ACTIVE_PAYLOAD) {
  getMock.mockResolvedValue({ data: payload });
  await useSkillGuide().setSession(UMO);
}

async function openByHover(wrapper: ReturnType<typeof mountItem>) {
  await wrapper.find('[data-test="skill-guide-menu-item"]').trigger("mouseenter");
  await flushPromises();
}

beforeEach(async () => {
  getMock.mockReset();
  postMock.mockReset();
  listMock.mockReset();
  useSkillGuide().reset();
  // showAll is a persisted preference living outside reset() — force it
  // back off so each test starts from the default rendering.
  await useSkillGuide().setShowAll(false);
});

afterEach(() => {
  useSkillGuide().reset();
  vi.useRealTimers();
});

describe("SkillGuideMenuItem — rendering & hover", () => {
  it("renders as a regular menu item and disables it without a session", () => {
    const wrapper = mountItem({ sessionId: null });
    const item = wrapper.find('[data-test="skill-guide-menu-item"]');
    expect(item.exists()).toBe(true);
    expect(item.attributes("disabled")).toBeDefined();
  });

  it("opens the separate skill menu on hover and lists the session's skills", async () => {
    await primeSession();
    const wrapper = mountItem();
    expect(
      wrapper.find('[data-test="skill-guide-item-brainstorming"]').exists(),
    ).toBe(false);

    await openByHover(wrapper);

    expect(wrapper.text()).toContain("brainstorming");
    expect(wrapper.text()).toContain("pdf");
  });

  it("also opens on click", async () => {
    await primeSession();
    const wrapper = mountItem();
    await wrapper.find('[data-test="skill-guide-menu-item"]').trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("brainstorming");
  });

  it("closes via the corner close button", async () => {
    await primeSession();
    const wrapper = mountItem();
    await openByHover(wrapper);
    expect(
      wrapper.find('[data-test="skill-guide-menu-card"]').exists(),
    ).toBe(true);

    await wrapper.find('[data-test="skill-guide-close"]').trigger("click");
    await flushPromises();
    expect(
      wrapper.find('[data-test="skill-guide-menu-card"]').exists(),
    ).toBe(false);
  });

  it("stays open when the pointer leaves, even after a skill was selected", async () => {
    await primeSession();
    const wrapper = mountItem();
    await openByHover(wrapper);

    // Interact with the content: select a skill (real timers here — the
    // singleton's async toggle must complete before we fake timers).
    postMock.mockResolvedValue(okLoad("brainstorming"));
    await wrapper
      .find('[data-test="skill-guide-item-brainstorming"]')
      .trigger("click");
    await flushPromises();
    expect(
      wrapper.find('[data-test="skill-guide-item-brainstorming"]').exists(),
    ).toBe(true);

    // Leaving the card or the item never closes the menu — not even
    // after a delayed wait (no hidden close timer). Dismissal is
    // explicit only (outside click / Escape / re-click on the activator).
    vi.useFakeTimers();
    await wrapper
      .find('[data-test="skill-guide-menu-card"]')
      .trigger("mouseleave");
    await wrapper
      .find('[data-test="skill-guide-menu-item"]')
      .trigger("mouseleave");
    await vi.advanceTimersByTimeAsync(200);
    expect(
      wrapper.find('[data-test="skill-guide-item-brainstorming"]').exists(),
    ).toBe(true);
  });
});

describe("SkillGuideMenuItem — independent menu & size caps", () => {
  it("is a separate v-menu (not part of the '+' menu layout) with teleported content", () => {
    const source = readFileSync(
      resolve("src/components/chat/SkillGuideMenuItem.vue"),
      "utf-8",
    );
    // The component root is its own <v-menu>, self-managed via v-model.
    expect(source).toMatch(/<v-menu/);
    expect(source).toMatch(/v-model="open"/);
    // Content is a plain card decoupled from the '+' menu's v-list layout.
    expect(source).toMatch(/class="skill-guide-menu-card"/);
  });

  it("caps height (scroll) and width (ellipsis truncation)", () => {
    const source = readFileSync(
      resolve("src/components/chat/SkillGuideMenuItem.vue"),
      "utf-8",
    );
    const listRule =
      source.match(/\.skill-guide-menu-card__list\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(listRule).toMatch(/max-height:\s*min\(48vh,\s*320px\)/);
    expect(listRule).toMatch(/overflow-y:\s*auto/);

    const cardRule =
      source.match(/\.skill-guide-menu-card\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(cardRule).toMatch(/min-width:\s*300px/);
    expect(cardRule).toMatch(/max-width:\s*340px/);

    const descRule =
      source.match(/\.skill-guide-menu-card__item-desc\s*\{([^}]*)\}/)?.[1] ??
      "";
    expect(descRule).toMatch(/text-overflow:\s*ellipsis/);
    expect(descRule).toMatch(/white-space:\s*nowrap/);
  });
});

describe("SkillGuideMenuItem — list states & queueing", () => {
  it("shows the empty state when the session has no active skills", async () => {
    await primeSession({
      status: "ok",
      data: { persona: null, skills: [] },
    });
    const wrapper = mountItem();
    await openByHover(wrapper);
    expect(wrapper.find('[data-test="skill-guide-empty"]').exists()).toBe(true);
  });

  it("shows the load-failed state when the plugin cannot be reached", async () => {
    getMock.mockRejectedValue(new Error("404"));
    await useSkillGuide().setSession(UMO);
    const wrapper = mountItem();
    await openByHover(wrapper);
    expect(wrapper.find('[data-test="skill-guide-load-failed"]').exists()).toBe(
      true,
    );
  });

  it("queues a skill via POST /skill-guide/load and marks it queued", async () => {
    await primeSession();
    postMock.mockResolvedValue(okLoad("brainstorming"));
    const wrapper = mountItem();
    await openByHover(wrapper);

    await wrapper
      .find('[data-test="skill-guide-item-brainstorming"]')
      .trigger("click");
    await flushPromises();

    expect(postMock).toHaveBeenCalledWith("skill-guide/load", {
      umo: UMO,
      skill_name: "brainstorming",
    });
    const item = wrapper.find('[data-test="skill-guide-item-brainstorming"]');
    expect(item.classes()).toContain(
      "skill-guide-menu-card__item--queued",
    );
    expect(item.text()).toContain("input.skillGuide.queued");
    expect(wrapper.find('[data-test="skill-guide-pop-count"]').text()).toBe(
      "1",
    );
  });

  it("un-queues a SINGLE skill and keeps the other selected skills", async () => {
    await primeSession();
    const wrapper = mountItem();
    await openByHover(wrapper);

    // Select two skills.
    postMock.mockResolvedValue(okLoad("brainstorming"));
    await wrapper
      .find('[data-test="skill-guide-item-brainstorming"]')
      .trigger("click");
    await flushPromises();
    postMock.mockResolvedValue(okLoad("pdf"));
    await wrapper.find('[data-test="skill-guide-item-pdf"]').trigger("click");
    await flushPromises();
    expect(useSkillGuide().queued.value).toEqual(["brainstorming", "pdf"]);

    // Un-select one: clear the whole server queue, then re-queue the rest.
    postMock.mockReset();
    postMock
      .mockResolvedValueOnce(okClear(["brainstorming", "pdf"]))
      .mockResolvedValueOnce(okLoad("pdf"));
    await wrapper
      .find('[data-test="skill-guide-item-brainstorming"]')
      .trigger("click");
    await flushPromises();

    expect(useSkillGuide().queued.value).toEqual(["pdf"]);
    expect(postMock.mock.calls).toEqual([
      ["skill-guide/clear", { umo: UMO }],
      ["skill-guide/load", { umo: UMO, skill_name: "pdf" }],
    ]);
    expect(
      wrapper
        .find('[data-test="skill-guide-item-brainstorming"]')
        .classes(),
    ).not.toContain("skill-guide-menu-card__item--queued");
    expect(
      wrapper.find('[data-test="skill-guide-item-pdf"]').classes(),
    ).toContain("skill-guide-menu-card__item--queued");
  });

  it("clears the whole queue via POST /skill-guide/clear", async () => {
    await primeSession();
    postMock.mockResolvedValue(okLoad("pdf"));
    await useSkillGuide().toggleSkill("pdf");

    const wrapper = mountItem();
    await openByHover(wrapper);

    postMock.mockReset();
    postMock.mockResolvedValue(okClear(["pdf"]));
    await wrapper.find('[data-test="skill-guide-clear-all"]').trigger("click");
    await flushPromises();

    expect(postMock).toHaveBeenCalledWith("skill-guide/clear", { umo: UMO });
    expect(
      wrapper.find('[data-test="skill-guide-item-pdf"]').classes(),
    ).not.toContain("skill-guide-menu-card__item--queued");
    expect(wrapper.find('[data-test="skill-guide-pop-count"]').exists()).toBe(
      false,
    );
  });
});

describe("SkillGuideMenuItem — show-all mode", () => {
  it("defaults to persona-mounted skills only", async () => {
    // The global /skills list is warmed on session mount to feed the "/"
    // palette (see useSkillGuide.candidateSkills); the popover must still
    // render persona-mounted rows only while show-all is off.
    listMock.mockResolvedValue(ALL_SKILLS_PAYLOAD);
    await primeSession();
    const wrapper = mountItem();
    await openByHover(wrapper);

    expect(wrapper.text()).toContain("brainstorming");
    expect(wrapper.text()).not.toContain("grilling");
  });

  it("lists every data/skills skill on toggle, graying unmounted ones after the mounted ones", async () => {
    // Resolve the global /skills mock before priming so the session-mount
    // warm-up fetch succeeds (show-all then renders the cached list).
    listMock.mockResolvedValue(ALL_SKILLS_PAYLOAD);
    await primeSession();
    const wrapper = mountItem();
    await openByHover(wrapper);

    await wrapper.find('[data-test="skill-guide-show-all"]').trigger("click");
    await flushPromises();

    // The list may come from the session-mount warm-up cache, so assert
    // the rendered result instead of a fresh /skills call.
    expect(wrapper.text()).toContain("grilling");
    const grilling = wrapper.find('[data-test="skill-guide-item-grilling"]');
    expect(grilling.classes()).toContain(
      "skill-guide-menu-card__item--unmounted",
    );
    expect(
      wrapper.find('[data-test="skill-guide-item-brainstorming"]').classes(),
    ).not.toContain("skill-guide-menu-card__item--unmounted");
    // Mounted rows first, gray rows after.
    const rows = wrapper.findAll(".skill-guide-menu-card__item");
    const dataTests = rows.map((row) => row.attributes("data-test"));
    expect(
      dataTests.indexOf("skill-guide-item-grilling"),
    ).toBeGreaterThan(dataTests.indexOf("skill-guide-item-brainstorming"));
    // Preference is persisted.
    expect(localStorage.getItem("chat.skillGuide.showAll")).toBe("1");
  });

  it("queues an unmounted (gray) skill via POST /skill-guide/load", async () => {
    listMock.mockResolvedValue(ALL_SKILLS_PAYLOAD);
    await primeSession();
    postMock.mockResolvedValue(okLoad("grilling"));
    const wrapper = mountItem();
    await openByHover(wrapper);

    await wrapper.find('[data-test="skill-guide-show-all"]').trigger("click");
    await flushPromises();
    await wrapper
      .find('[data-test="skill-guide-item-grilling"]')
      .trigger("click");
    await flushPromises();

    expect(postMock).toHaveBeenCalledWith("skill-guide/load", {
      umo: UMO,
      skill_name: "grilling",
    });
    expect(useSkillGuide().queued.value).toEqual(["grilling"]);
    expect(
      wrapper.find('[data-test="skill-guide-item-grilling"]').classes(),
    ).toContain("skill-guide-menu-card__item--queued");
  });
});

describe("SkillGuideMenuItem — per-open refresh & search", () => {
  it("re-fetches the active skill list every time the menu opens", async () => {
    await primeSession();
    expect(getMock).toHaveBeenCalledTimes(1);
    const wrapper = mountItem();

    // First open: the menu triggers a fresh /skill-guide/active fetch.
    await openByHover(wrapper);
    expect(getMock).toHaveBeenCalledTimes(2);

    // Explicitly dismiss the menu (the update:modelValue=false that
    // Vuetify emits on outside click / Escape — pointer leave no longer
    // closes it) and hover again: a second open triggers a third fetch.
    await wrapper.findComponent(menuStub).vm.$emit("update:modelValue", false);
    await flushPromises();
    expect(
      wrapper.find('[data-test="skill-guide-menu-card"]').exists(),
    ).toBe(false);
    await wrapper
      .find('[data-test="skill-guide-menu-item"]')
      .trigger("mouseenter");
    await flushPromises();
    expect(getMock).toHaveBeenCalledTimes(3);
  });

  it("filters the skill list by name or description", async () => {
    await primeSession();
    const wrapper = mountItem();
    await openByHover(wrapper);
    expect(wrapper.findAll('[data-test^="skill-guide-item-"]')).toHaveLength(2);

    // Case-insensitive description match.
    await wrapper.find('[data-test="skill-guide-search"]').setValue("PDF");
    expect(wrapper.find('[data-test="skill-guide-item-pdf"]').exists()).toBe(
      true,
    );
    expect(
      wrapper.find('[data-test="skill-guide-item-brainstorming"]').exists(),
    ).toBe(false);

    // No match renders the inline no-match hint.
    await wrapper.find('[data-test="skill-guide-search"]').setValue("zzz");
    expect(wrapper.find('[data-test="skill-guide-no-match"]').exists()).toBe(
      true,
    );

    // Clear button restores the full list.
    await wrapper
      .find('[data-test="skill-guide-search-clear"]')
      .trigger("click");
    expect(wrapper.findAll('[data-test^="skill-guide-item-"]')).toHaveLength(2);
  });
});

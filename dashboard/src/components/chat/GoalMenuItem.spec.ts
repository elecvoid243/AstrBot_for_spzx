// GoalMenuItem.spec.ts
// Author: elecvoid243 @ 2026-10-25
//
// Unit tests for the "设定目标" entry in the chat input's "+" menu
// (kernel /goal discovery). Covers: compact no-goal label, active-goal
// label + count trail, open-card flow, start-disabled validation,
// budget stepper clamping + dirty flag, API error surfacing, and the
// active-goal click routing to the composer bar's edit dialog.
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { computed, defineComponent } from "vue";

vi.mock("@/api/v1", () => ({
  chatApi: { setSessionGoal: vi.fn() },
}));

// Self-contained i18n mock (returns the key), same shape as
// SkillGuideMenuItem.spec.ts.
const tmMock = vi.fn((key: string) => key);
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: tmMock, getRaw: vi.fn() }),
}));

import { chatApi } from "@/api/v1";
import { useChatHeaderStore } from "@/stores/chatHeader";
import GoalMenuItem from "./GoalMenuItem.vue";

const setGoalMock = chatApi.setSessionGoal as ReturnType<typeof vi.fn>;

const UMO = "sess-1";

// v-menu stub: driven by v-model (the component owns `open` via its
// activator click override), content renders only while open.
const menuStub = defineComponent({
  props: { modelValue: { type: Boolean, default: false } },
  emits: ["update:modelValue"],
  setup(props) {
    const open = computed(() => props.modelValue);
    return { open };
  },
  template: `<div class="v-menu-stub"><slot name="activator" :props="{ onClick: () => $emit('update:modelValue', true) }" /><slot v-if="open" /></div>`,
});

const stubs = {
  "v-menu": menuStub,
  "v-icon": { template: "<i><slot /></i>" },
  "v-list-item": {
    props: ["disabled"],
    emits: ["click"],
    template: `<div v-bind="$attrs" class="v-list-item-stub" :disabled="disabled" @click="$emit('click')"><slot name="prepend" /><slot /><slot name="append" /></div>`,
  },
  "v-list-item-title": { template: "<span><slot /></span>" },
  "v-btn": {
    props: ["disabled", "loading"],
    emits: ["click"],
    template: `<button v-bind="$attrs" class="v-btn-stub" :disabled="disabled || loading" @click="$emit('click')"><slot /></button>`,
  },
};

function mountItem() {
  return mount(GoalMenuItem, {
    props: { sessionId: UMO, disabled: false },
    global: { stubs },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  setGoalMock.mockReset();
  tmMock.mockClear();
});

describe("GoalMenuItem — labels", () => {
  it("shows only the set-goal label without a goal", () => {
    const wrapper = mountItem();
    const label = wrapper.find('[data-test="goal-menu-label"]');
    expect(label.text()).toBe("goal.menuSet");
    expect(wrapper.find('[data-test="goal-menu-trail"]').exists()).toBe(false);
    expect(wrapper.text()).not.toContain("goal.menuHint");
  });

  it("shows the goal label with status and count trail when a goal exists", () => {
    const chatHeader = useChatHeaderStore();
    chatHeader.goalBadge = { status: "active", turnsUsed: 3, maxTurns: 20 };
    const wrapper = mountItem();
    expect(wrapper.find('[data-test="goal-menu-label"]').text()).toBe(
      "goal.menuLabel · goal.status_active",
    );
    expect(wrapper.find('[data-test="goal-menu-trail"]').text()).toContain(
      "3/20",
    );
  });
});

describe("GoalMenuItem — set flow (no goal)", () => {
  it("opens the card on activator click and closes via the close button", async () => {
    const wrapper = mountItem();
    await wrapper.find(".v-list-item-stub").trigger("click");
    expect(wrapper.find('[data-test="goal-menu-card"]').exists()).toBe(true);

    await wrapper.find('[data-test="goal-card-close"]').trigger("click");
    expect(wrapper.find('[data-test="goal-menu-card"]').exists()).toBe(false);
  });

  it("keeps start disabled until the goal text is non-empty", async () => {
    const wrapper = mountItem();
    await wrapper.find(".v-list-item-stub").trigger("click");

    const start = () => wrapper.find('[data-test="goal-start"]');
    expect(start().attributes("disabled")).toBeDefined();

    await wrapper
      .find('[data-test="goal-textarea"]')
      .setValue("完成鉴权重构");
    expect(start().attributes("disabled")).toBeUndefined();
  });

  it("sends the goal without a budget override when the stepper is untouched", async () => {
    setGoalMock.mockResolvedValue({
      data: { status: "ok", data: { goal: { goal_id: "g1" } } },
    });
    const wrapper = mountItem();
    await wrapper.find(".v-list-item-stub").trigger("click");
    await wrapper
      .find('[data-test="goal-textarea"]')
      .setValue("完成鉴权重构");
    await wrapper.find('[data-test="goal-start"]').trigger("click");
    await flushPromises();

    expect(setGoalMock).toHaveBeenCalledTimes(1);
    const [sessionId, goal, maxTurns] = setGoalMock.mock.calls[0];
    expect(sessionId).toBe(UMO);
    expect(goal).toBe("完成鉴权重构");
    expect(maxTurns).toBeUndefined();
    // Card closes after success; state converges via the push.
    expect(wrapper.find('[data-test="goal-menu-card"]').exists()).toBe(false);
  });

  it("sends the stepped budget only after the stepper is touched", async () => {
    setGoalMock.mockResolvedValue({
      data: { status: "ok", data: { goal: { goal_id: "g1" } } },
    });
    const wrapper = mountItem();
    await wrapper.find(".v-list-item-stub").trigger("click");
    await wrapper
      .find('[data-test="goal-textarea"]')
      .setValue("完成鉴权重构");
    await wrapper.find('[data-test="goal-budget-plus"]').trigger("click");
    await wrapper.find('[data-test="goal-budget-plus"]').trigger("click");
    expect(wrapper.find(".goal-menu-card__budget-value").text()).toBe("22");

    await wrapper.find('[data-test="goal-start"]').trigger("click");
    await flushPromises();

    const [, , maxTurns] = setGoalMock.mock.calls[0];
    expect(maxTurns).toBe(22);
  });

  it("clamps the stepper to its bounds", async () => {
    const wrapper = mountItem();
    await wrapper.find(".v-list-item-stub").trigger("click");

    for (let i = 0; i < 30; i += 1) {
      await wrapper.find('[data-test="goal-budget-minus"]').trigger("click");
    }
    expect(wrapper.find(".goal-menu-card__budget-value").text()).toBe("5");

    for (let i = 0; i < 200; i += 1) {
      await wrapper.find('[data-test="goal-budget-plus"]').trigger("click");
    }
    expect(wrapper.find(".goal-menu-card__budget-value").text()).toBe("100");
  });

  it("surfaces an API error and keeps the card open", async () => {
    setGoalMock.mockRejectedValue(new Error("boom"));
    const wrapper = mountItem();
    await wrapper.find(".v-list-item-stub").trigger("click");
    await wrapper
      .find('[data-test="goal-textarea"]')
      .setValue("完成鉴权重构");
    await wrapper.find('[data-test="goal-start"]').trigger("click");
    await flushPromises();

    expect(wrapper.find(".goal-menu-card__error").text()).toBe(
      "goal.setFailed",
    );
    expect(wrapper.find('[data-test="goal-menu-card"]').exists()).toBe(true);
  });
});

describe("GoalMenuItem — active goal routing", () => {
  it("opens the composer bar's edit dialog instead of the card when a goal exists", async () => {
    const chatHeader = useChatHeaderStore();
    chatHeader.goalBadge = { status: "active", turnsUsed: 3, maxTurns: 20 };
    const editSpy = vi.spyOn(chatHeader, "SET_GOAL_EDIT_OPEN");

    const wrapper = mountItem();
    await wrapper.find(".v-list-item-stub").trigger("click");

    expect(editSpy).toHaveBeenCalledWith(true);
    expect(wrapper.find('[data-test="goal-menu-card"]').exists()).toBe(false);
  });
});

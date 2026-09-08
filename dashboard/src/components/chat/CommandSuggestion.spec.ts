// CommandSuggestion.spec.ts
// Author: elecvoid243 @ 2026-09-08
//
// Rendering tests for the "/" palette badges added for skill
// pseudo-commands (kind="skill" / queued). The panel itself is unchanged.
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CommandSuggestion, {
  type SuggestionCommand,
} from "./CommandSuggestion.vue";

function command(
  effective: string,
  overrides: Partial<SuggestionCommand> = {},
): SuggestionCommand {
  return {
    handler_full_name: `cmd:${effective}`,
    effective_command: effective,
    description: "",
    plugin_display_name: null,
    enabled: true,
    reserved: false,
    ...overrides,
  };
}

function skill(
  name: string,
  overrides: Partial<SuggestionCommand> = {},
): SuggestionCommand {
  return command(`/${name}`, {
    kind: "skill",
    description: `${name} skill`,
    ...overrides,
  });
}

function mountPanel(commands: SuggestionCommand[]) {
  return mount(CommandSuggestion, {
    props: { visible: true, commands, selectedIndex: 0, isDark: false },
  });
}

describe("CommandSuggestion — skill badges", () => {
  it("tags a skill row and leaves plain commands untouched", () => {
    const wrapper = mountPanel([
      command("/help", { plugin_display_name: "Core" }),
      skill("brainstorming"),
    ]);

    const rows = wrapper.findAll(".command-suggestion-item");
    expect(rows).toHaveLength(2);

    // Plain command: plugin name badge, no skill tag.
    expect(rows[0].find(".command-plugin").text()).toBe("Core");
    expect(rows[0].find(".command-plugin--skill").exists()).toBe(false);

    // Skill: "Skill" tag instead of a plugin name.
    expect(rows[1].find(".command-plugin--skill").text()).toContain("Skill");
    expect(rows[1].find(".command-name").text()).toBe("/brainstorming");
  });

  it("marks an already queued skill", () => {
    const wrapper = mountPanel([skill("pdf", { queued: true })]);

    expect(wrapper.find(".command-plugin--skill").exists()).toBe(true);
    expect(wrapper.find(".command-plugin--queued").text()).toContain("已加载");
  });

  it("renders nothing while hidden", () => {
    const wrapper = mount(CommandSuggestion, {
      props: {
        visible: false,
        commands: [skill("pdf")],
        selectedIndex: 0,
        isDark: false,
      },
    });

    expect(wrapper.find(".command-suggestion-panel").exists()).toBe(false);
  });
});

// suggestionMerge.spec.ts
// Author: elecvoid243 @ 2026-09-08
//
// Unit tests for the composer "/" palette merge: enabled bot commands +
// skill pseudo-commands (astrbot_plugin_skill_guide).
import { describe, expect, it } from "vitest";

import {
  DEFAULT_MAX_SKILLS_WHEN_EMPTY,
  mergeSuggestions,
  normalizeCommandSearchText,
  stripLeadingTriggerToken,
  stripWakePrefix,
  type MergeSuggestionsInput,
} from "./suggestionMerge";
import type { SuggestionCommand } from "./CommandSuggestion.vue";

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

function baseInput(
  overrides: Partial<MergeSuggestionsInput> = {},
): MergeSuggestionsInput {
  return {
    commands: [],
    skills: [],
    text: "/",
    wakePrefixes: ["/"],
    ...overrides,
  };
}

describe("stripWakePrefix / normalizeCommandSearchText", () => {
  it("strips the first matching wake prefix", () => {
    expect(stripWakePrefix("/help", ["/"])).toBe("help");
    expect(stripWakePrefix("!help", ["!", "/"])).toBe("help");
  });

  it("keeps the text untouched when no prefix matches", () => {
    expect(stripWakePrefix("!help", ["/"])).toBe("!help");
  });

  it("trims and lowercases for matching", () => {
    expect(normalizeCommandSearchText("  /Help ", ["/"])).toBe("help");
  });
});

describe("stripLeadingTriggerToken", () => {
  it("removes the typed query even when it is only a prefix of the picked name", () => {
    // Selecting "writing-plans" after typing "/wr" must clear "/wr".
    expect(stripLeadingTriggerToken("/wr", ["/"])).toBe("");
  });

  it("removes the full command token", () => {
    expect(stripLeadingTriggerToken("/writing-plans", ["/"])).toBe("");
  });

  it("keeps text typed after the trigger token", () => {
    expect(stripLeadingTriggerToken("/wr 帮我想个方案", ["/"])).toBe(
      "帮我想个方案",
    );
  });

  it("clears a bare wake prefix", () => {
    expect(stripLeadingTriggerToken("/", ["/"])).toBe("");
  });

  it("leaves text without a wake prefix untouched", () => {
    expect(stripLeadingTriggerToken("帮我想 /wr", ["/"])).toBe("帮我想 /wr");
  });

  it("honours a custom wake prefix", () => {
    expect(stripLeadingTriggerToken("!wr 帮", ["!"])).toBe("帮");
  });
});

describe("mergeSuggestions", () => {
  it("lists every command (reserved first) then skills alphabetically when the query is empty", () => {
    const result = mergeSuggestions(
      baseInput({
        commands: [
          command("/zebra"),
          command("/help", { reserved: true }),
        ],
        skills: [skill("pdf"), skill("brainstorming")],
      }),
    );

    expect(result.map((cmd) => cmd.effective_command)).toEqual([
      "/help",
      "/zebra",
      "/brainstorming",
      "/pdf",
    ]);
  });

  it("caps the skills appended for an empty query", () => {
    const skills = Array.from({ length: 12 }, (_, i) =>
      skill(`skill-${String(i).padStart(2, "0")}`),
    );
    const result = mergeSuggestions(baseInput({ skills }));

    expect(result).toHaveLength(DEFAULT_MAX_SKILLS_WHEN_EMPTY);
    expect(result[0].effective_command).toBe("/skill-00");
  });

  it("ranks startsWith matches above substring matches", () => {
    const result = mergeSuggestions(
      baseInput({
        commands: [command("/reload")],
        skills: [skill("load-skill")],
        text: "/load",
      }),
    );

    expect(result.map((cmd) => cmd.effective_command)).toEqual([
      "/load-skill",
      "/reload",
    ]);
  });

  it("keeps commands ahead of equally-matching skills", () => {
    const result = mergeSuggestions(
      baseInput({
        commands: [command("/plan")],
        skills: [skill("planning")],
        text: "/plan",
      }),
    );

    expect(result.map((cmd) => cmd.effective_command)).toEqual([
      "/plan",
      "/planning",
    ]);
  });

  it("matches skill descriptions too", () => {
    const result = mergeSuggestions(
      baseInput({
        skills: [
          skill("brainstorming", {
            description: "Explore intent before implementation",
          }),
        ],
        text: "/intent",
      }),
    );

    expect(result.map((cmd) => cmd.effective_command)).toEqual([
      "/brainstorming",
    ]);
  });

  it("sorts reserved commands first inside the substring bucket", () => {
    const result = mergeSuggestions(
      baseInput({
        commands: [
          command("/beta-play"),
          command("/alpha-play", { reserved: true }),
        ],
        text: "/play",
      }),
    );

    expect(result.map((cmd) => cmd.effective_command)).toEqual([
      "/alpha-play",
      "/beta-play",
    ]);
  });

  it("honours a custom wake prefix", () => {
    const result = mergeSuggestions(
      baseInput({
        skills: [command("!brainstorming", { kind: "skill" })],
        text: "!brain",
        wakePrefixes: ["!"],
      }),
    );

    expect(result.map((cmd) => cmd.effective_command)).toEqual([
      "!brainstorming",
    ]);
  });
});

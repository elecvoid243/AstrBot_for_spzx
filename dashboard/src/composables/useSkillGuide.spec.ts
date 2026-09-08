// useSkillGuide.spec.ts
// Author: elecvoid243 @ 2026-09-08
//
// Unit tests for the "/" palette additions to the Skill Guide singleton:
// candidateSkills (session-effective ∪ globally enabled ∪ show-all) and
// queueSkill, the writer used by the slash-command select path.
import { flushPromises } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: vi.fn(), post: vi.fn() },
  skillApi: { list: vi.fn() },
}));

import { pluginExtensionApi, skillApi } from "@/api/v1";
import { useSkillGuide } from "@/composables/useSkillGuide";

const getMock = pluginExtensionApi.get as ReturnType<typeof vi.fn>;
const postMock = pluginExtensionApi.post as ReturnType<typeof vi.fn>;
const listMock = skillApi.list as ReturnType<typeof vi.fn>;

const UMO = "webchat:FriendMessage:webchat!alice!sess-1";

// GET /skill-guide/active: session-effective (persona-mounted) skills.
const ACTIVE_PAYLOAD = {
  status: "ok",
  data: {
    persona: null,
    skills: [
      {
        name: "pdf",
        description: "Work with PDF files",
        path: "skills/pdf",
        source_type: "local_only",
      },
    ],
  },
};

// Core GET /skills: every skill on disk. "grilling" is globally disabled
// and "plug-skill" belongs to a disabled plugin — both must stay out of
// the palette unless "show all" is on. (`skillApi.list()` resolves to the
// axios response, so the payload lives at res.data.data.)
const ALL_SKILLS = [
  {
    name: "pdf",
    description: "Work with PDF files",
    path: "skills/pdf/SKILL.md",
    source_type: "local_only",
    active: true,
  },
  {
    name: "brainstorming",
    description: "Explore intent before implementation",
    path: "skills/brainstorming/SKILL.md",
    source_type: "local_only",
    active: true,
  },
  {
    name: "grilling",
    description: "Deep-dive questioning",
    path: "skills/grilling/SKILL.md",
    source_type: "local_only",
    active: false,
  },
  {
    name: "plug-skill",
    description: "Provided by a disabled plugin",
    path: "skills/plug-skill/SKILL.md",
    source_type: "plugin",
    active: true,
    plugin_active: false,
  },
];

const guide = useSkillGuide();

async function primeSession() {
  getMock.mockResolvedValue({ data: ACTIVE_PAYLOAD });
  listMock.mockResolvedValue({ data: { data: { skills: ALL_SKILLS } } });
  await guide.setSession(UMO);
  // setSession warms the cache fire-and-forget; refetch deterministically.
  await guide.refreshAllSkills();
  await flushPromises();
}

function candidateNames(): string[] {
  return guide.candidateSkills.value.map((skill) => skill.name);
}

beforeEach(async () => {
  getMock.mockReset();
  postMock.mockReset();
  listMock.mockReset();
  guide.reset();
  // showAll is a persisted preference outside reset() — force it off.
  await guide.setShowAll(false);
});

describe("candidateSkills", () => {
  it("unions session-effective and globally enabled skills, deduped by name", async () => {
    await primeSession();

    expect(candidateNames()).toEqual(["pdf", "brainstorming"]);
    // Session-effective entries come first and stay flagged as mounted.
    expect(guide.candidateSkills.value[0].mounted).toBe(true);
    expect(guide.candidateSkills.value[1].mounted).toBe(false);
  });

  it("hides globally disabled and disabled-plugin skills", async () => {
    await primeSession();

    expect(candidateNames()).not.toContain("grilling");
    expect(candidateNames()).not.toContain("plug-skill");
  });

  it("includes every skill on disk when show-all is on", async () => {
    await primeSession();
    await guide.setShowAll(true);
    await flushPromises();

    expect(candidateNames()).toEqual([
      "pdf",
      "brainstorming",
      "grilling",
      "plug-skill",
    ]);
  });
});

describe("queueSkill", () => {
  it("POSTs /skill-guide/load and mirrors the queue locally", async () => {
    await primeSession();
    postMock.mockResolvedValue({
      data: { status: "ok", data: { skill_name: "pdf", queued: true } },
    });

    await expect(guide.queueSkill("pdf")).resolves.toBe(true);
    expect(postMock).toHaveBeenCalledWith("skill-guide/load", {
      umo: UMO,
      skill_name: "pdf",
    });
    expect(guide.queued.value).toEqual(["pdf"]);
  });

  it("soft-fails without touching the local queue", async () => {
    await primeSession();
    postMock.mockRejectedValue(new Error("boom"));

    await expect(guide.queueSkill("pdf")).resolves.toBe(false);
    expect(guide.queued.value).toEqual([]);
  });

  it("refuses to queue without a session", async () => {
    await expect(guide.queueSkill("pdf")).resolves.toBe(false);
    expect(postMock).not.toHaveBeenCalled();
  });
});

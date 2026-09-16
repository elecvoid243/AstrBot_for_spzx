// useSpcodeSession.spec.ts
//
// The session context makes a component's session identity explicit.
// Without a provider (standalone composables, specs, display-only chips
// outside the chat page) it falls back to the shared status ref, which
// keeps existing call sites working unchanged.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { computed, defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: vi.fn(), post: vi.fn() },
}));

import { useSpcodeProjectStatus } from "./useSpcodeProjectStatus";
import { provideSpcodeSession, useSpcodeSession } from "./useSpcodeSession";

const UMO_A = "webchat:FriendMessage:webchat!astrbot!cid-A";

describe("useSpcodeSession", () => {
  beforeEach(() => {
    useSpcodeProjectStatus().reset();
  });

  it("resolves the provided session context", () => {
    let seen: string | null = null;
    let scoped = false;
    const Child = defineComponent({
      setup() {
        const session = useSpcodeSession();
        scoped = session.scoped;
        return () => {
          seen = session.umo.value;
          return h("div");
        };
      },
    });
    const Parent = defineComponent({
      setup() {
        provideSpcodeSession({
          umo: computed(() => UMO_A),
          directory: computed(() => "C:/proj/a"),
        });
        return () => h(Child);
      },
    });

    mount(Parent);

    expect(seen).toBe(UMO_A);
    expect(scoped).toBe(true);
  });

  it("falls back to the shared status outside a provider", () => {
    const spcodeStatus = useSpcodeProjectStatus();
    spcodeStatus.setLoaded(UMO_A, "C:/proj/a");
    spcodeStatus.setActiveUmo(UMO_A);

    const session = useSpcodeSession();

    expect(session.scoped).toBe(false);
    expect(session.umo.value).toBe(UMO_A);
    expect(session.directory.value).toBe("C:/proj/a");
  });

  it("warns in dev when requireScoped falls back", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    useSpcodeSession({ requireScoped: true });
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("[useSpcodeSession]"),
    );
    warn.mockRestore();
  });
});

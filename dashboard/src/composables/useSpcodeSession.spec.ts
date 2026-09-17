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
import { useSpcodeFileWrite } from "./useSpcodeFileWrite";
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

  it("warns when a repository-write composable runs in a component with no provider", () => {
    // Simulate a dev build: vitest runs with MODE="test", where the
    // warning is suppressed so component-hosting suites stay quiet.
    vi.stubEnv("MODE", "development");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    // Dev warning targets the real mistake: a component tree that never
    // called provideSpcodeSession(), so a repository write would silently
    // address the shared status instead of this session. Going through a
    // real write-path composable also pins that it opts into
    // requireScoped — drop that flag and this test fails.
    const Orphan = defineComponent({
      setup() {
        useSpcodeFileWrite();
        return () => h("div");
      },
    });

    mount(Orphan);

    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("[useSpcodeSession]"),
    );
    warn.mockRestore();
    vi.unstubAllEnvs();
  });

  it("stays quiet inside a component that provided the session", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const Child = defineComponent({
      setup() {
        useSpcodeSession({ requireScoped: true });
        return () => h("div");
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

    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("stays quiet in the test environment even inside a provider-less component", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    // Pins the deliberate suppression: several existing suites mount a
    // throwaway component purely to host a composable, so warning there
    // would flood test output with a misuse that does not exist.
    const Orphan = defineComponent({
      setup() {
        useSpcodeFileWrite();
        return () => h("div");
      },
    });

    mount(Orphan);

    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("stays quiet outside a component even with requireScoped", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    // Keeps the many component-less call sites (composable specs,
    // standalone utilities) from spamming stderr with a warning they
    // cannot act on.
    const session = useSpcodeSession({ requireScoped: true });

    expect(session.scoped).toBe(false);
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });
});

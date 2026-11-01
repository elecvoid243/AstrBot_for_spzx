import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EffectScope } from "vue";

const scopes: EffectScope[] = [];

async function createSessionSelection(initialSessionId = "") {
  const { effectScope, ref } = await import("vue");
  const { useProviderModelSelection } = await import(
    "./useProviderModelSelection"
  );
  const scope = effectScope();
  scopes.push(scope);
  const sessionId = ref(initialSessionId);
  const store = scope.run(() => useProviderModelSelection(sessionId))!;
  return { store, sessionId, scope, useProviderModelSelection };
}

describe("session provider model selection", () => {
  it("resets shared choices and isolates saved models when accounts change", async () => {
    localStorage.setItem("account_id", "alice");
    const { store } = await createSessionSelection("a");
    store.setSelection("alice-provider", "alice-model");
    const { resetAccountSession } = await import("@/utils/accountSession");
    resetAccountSession();
    localStorage.setItem("account_id", "bob");
    const bob = await createSessionSelection("b");
    expect(bob.store.selectedProviderId.value).toBe("");
    bob.store.setSelection("bob-provider", "bob-model");
    resetAccountSession();
    localStorage.setItem("account_id", "alice");
    const alice = await createSessionSelection("a");
    expect(alice.store.selectedModelName.value).toBe("alice-model");
  });

  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
  });

  afterEach(() => {
    scopes.splice(0).forEach((scope) => scope.stop());
    vi.restoreAllMocks();
  });

  it("shares the header choice with the composer and preserves global keys", async () => {
    localStorage.setItem("selectedProvider", "provider-old");
    localStorage.setItem("selectedProviderModel", "model-old");
    const { store, useProviderModelSelection } = await createSessionSelection();
    const header = useProviderModelSelection();
    expect(store.selectedProviderId.value).toBe("provider-old");
    header.setSelection("provider-new", "model-new");
    expect(store.selectedProviderId.value).toBe("provider-new");
    expect(store.selectedModelName.value).toBe("model-new");
    expect(localStorage.getItem("selectedProviderModel")).toBe("model-new");
  });

  it("restores both sessions before a same-tick request reads the choice", async () => {
    const { store, sessionId } = await createSessionSelection("a");
    store.setSelection("provider-a", "model-a");
    sessionId.value = "b";
    store.setSelection("provider-b", "model-b");
    sessionId.value = "a";
    expect(store.selectedProviderId.value).toBe("provider-a");
    expect(store.selectedModelName.value).toBe("model-a");
    sessionId.value = "b";
    expect(store.selectedProviderId.value).toBe("provider-b");
    expect(store.selectedModelName.value).toBe("model-b");
    expect(JSON.parse(localStorage.getItem("chat.sessionProvider.a")!)).toEqual(
      {
        providerId: "provider-a",
        modelName: "model-a",
      },
    );
  });

  it("does not attribute a queued choice to the next session", async () => {
    localStorage.setItem(
      "chat.sessionProvider.b",
      JSON.stringify({
        providerId: "provider-b",
        modelName: "model-b",
      }),
    );
    const { store, sessionId } = await createSessionSelection("a");
    store.setSelection("provider-a", "model-a");
    sessionId.value = "b";
    await (await import("vue")).nextTick();
    expect(JSON.parse(localStorage.getItem("chat.sessionProvider.a")!)).toEqual(
      {
        providerId: "provider-a",
        modelName: "model-a",
      },
    );
    expect(store.selectedModelName.value).toBe("model-b");
  });

  it("restores a persisted session on page load instead of the global choice", async () => {
    localStorage.setItem("selectedProvider", "provider-global");
    localStorage.setItem(
      "chat.sessionProvider.a",
      JSON.stringify({
        providerId: "provider-a",
        modelName: "model-a",
      }),
    );
    const { store } = await createSessionSelection("a");
    expect(store.selectedProviderId.value).toBe("provider-a");
    expect(store.selectedModelName.value).toBe("model-a");
  });

  it("keeps the last choice for new sessions and ignores corrupted entries", async () => {
    const { store, sessionId } = await createSessionSelection("a");
    store.setSelection("provider-a", "model-a");
    for (const raw of ["{", '{"providerId":17}', '{"providerId":""}']) {
      localStorage.setItem("chat.sessionProvider.b", raw);
      sessionId.value = "b";
      expect(store.selectedModelName.value).toBe("model-a");
      sessionId.value = "a";
    }
    sessionId.value = "new";
    expect(store.selectedProviderId.value).toBe("provider-a");
    expect(localStorage.getItem("chat.sessionProvider.new")).toBeNull();
  });

  it("records sends and explicit regeneration choices for their target session", async () => {
    const { store } = await createSessionSelection("active");
    store.setSelection("provider-main", "model-main");
    store.writeSessionProviderSelection("sent", {
      providerId: store.selectedProviderId.value,
      modelName: store.selectedModelName.value,
    });
    store.writeSessionProviderSelection("regenerated", {
      providerId: "provider-other",
      modelName: "model-other",
    });
    expect(
      JSON.parse(localStorage.getItem("chat.sessionProvider.sent")!),
    ).toEqual({
      providerId: "provider-main",
      modelName: "model-main",
    });
    expect(
      JSON.parse(localStorage.getItem("chat.sessionProvider.regenerated")!),
    ).toEqual({
      providerId: "provider-other",
      modelName: "model-other",
    });
    expect(store.selectedProviderId.value).toBe("provider-main");
  });

  it("ignores empty sessions and stops saving after the chat unmounts", async () => {
    const { store, sessionId, scope } = await createSessionSelection();
    store.setSelection("provider-main", "model-main");
    expect(localStorage.getItem("chat.sessionProvider.")).toBeNull();
    store.writeSessionProviderSelection("empty", null);
    expect(localStorage.getItem("chat.sessionProvider.empty")).toBeNull();
    sessionId.value = "a";
    scope.stop();
    store.setSelection("provider-other", "model-other");
    expect(localStorage.getItem("chat.sessionProvider.a")).toBeNull();
  });

  it("continues sharing a usable selection when storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("Storage blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Storage full");
    });
    const { store, sessionId, useProviderModelSelection } =
      await createSessionSelection("a");
    expect(() => store.setSelection("provider-a", "model-a")).not.toThrow();
    sessionId.value = "b";
    const composer = useProviderModelSelection();
    expect(composer.selectedProviderId.value).toBe("provider-a");
    expect(composer.selectedModelName.value).toBe("model-a");
  });
});

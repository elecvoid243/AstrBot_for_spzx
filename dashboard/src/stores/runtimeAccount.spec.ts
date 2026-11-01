import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useInteractiveChoiceStore } from "./interactiveChoice";
import { usePendingFollowUps } from "@/composables/usePendingFollowUps";
import { resetAccountSession } from "@/utils/accountSession";
import { httpClient } from "@/api/http";

vi.mock("@/api/http", () => ({ httpClient: { post: vi.fn() } }));
beforeEach(() => {
  setActivePinia(createPinia());
  resetAccountSession();
  localStorage.clear();
});

describe("runtime state on account changes", () => {
  it("removes queued follow-up text before the next account can render it", () => {
    const queue = usePendingFollowUps();
    queue.enqueue("owner-a-session", "private follow-up");
    resetAccountSession();
    expect(queue.itemsFor("owner-a-session")).toEqual([]);
    expect(Object.keys(queue.pendingBySession)).toEqual([]);
  });

  it("discards pending question reconciliation that resolves after logout", async () => {
    let complete!: (response: unknown) => void;
    vi.mocked(httpClient.post).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    const store = useInteractiveChoiceStore();
    const pending = store.reconcile(
      "webchat:FriendMessage:webchat!owner-a!session-a",
    );
    resetAccountSession();
    complete({
      data: {
        status: "ok",
        data: {
          pending: [
            {
              type: "interactive_choice",
              request_id: "old-secret",
              prompt: "private prompt",
              options: [],
            },
          ],
        },
      },
    });
    await pending;
    expect(store.activeChoices).toEqual({});
    expect(
      localStorage.getItem("astrbot-interactive-choice-pending"),
    ).toBeNull();
  });
});

import { describe, expect, it, vi } from "vitest";
import { effectScope, nextTick, ref } from "vue";
import { useSpcodeFileBrowser } from "./useSpcodeFileBrowser";

const get = vi.hoisted(() => vi.fn());
vi.mock("@/api/v1", () => ({ pluginExtensionApi: { get } }));

describe("file navigation request lifecycle", () => {
  it("shows plugin feedback for a missing extension's HTTP 200 error envelope", async () => {
    get.mockResolvedValue({ data: { status: "error", message: "route missing", data: {} } });
    const scope = effectScope();
    const browser = scope.run(() => useSpcodeFileBrowser(ref("G:/a.py"), true))!;
    await vi.waitFor(() => expect(browser.state.value).toEqual({
      kind: "error", reason: "spcode_unavailable", previousSnapshot: undefined,
    }));
    browser.dispose();
    scope.stop();
  });
  it("ignores late replies, clears old contents and reopens the same current disk target", async () => {
    const replies: Array<(value: unknown) => void> = [];
    get.mockImplementation(
      () => new Promise((resolve) => replies.push(resolve)),
    );
    const path = ref("G:/first.py");
    const request = ref(1);
    const scope = effectScope();
    const browser = scope.run(() => useSpcodeFileBrowser(path, true, request))!;
    path.value = "G:/second.py";
    request.value++;
    await nextTick();
    const response = (path: string, content: string) => ({
      data: { data: { type: "file", path, name: path, content } },
    });
    replies[1](response(path.value, "second"));
    await vi.waitFor(() => expect(browser.state.value.kind).toBe("file"));
    replies[0](response("G:/first.py", "wrong"));
    await nextTick();
    expect(browser.state.value).toMatchObject({
      kind: "file",
      snapshot: { content: "second" },
    });
    request.value++;
    await nextTick();
    expect(browser.state.value.kind).toBe("loading");
    expect(get.mock.lastCall?.[1].params).toEqual({
      path: "G:/second.py",
      scope: "host",
    });
    browser.dispose();
    replies[2](response(path.value, "after disposal"));
    await nextTick();
    expect(browser.state.value.kind).toBe("loading");
    scope.stop();
  });
});

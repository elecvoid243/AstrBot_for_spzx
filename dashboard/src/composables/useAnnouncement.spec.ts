import { afterEach, expect, it, vi } from "vitest";
import axios from "axios";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  localStorage.clear();
});

it("loads and reopens the shared announcement for an ordinary account", async () => {
  vi.resetModules();
  vi.useFakeTimers();
  localStorage.setItem("role", "user");
  const notice = { title: "Notice", content: "Welcome", enabled: true, version: 1 };
  const get = vi.spyOn(axios, "get").mockResolvedValue({ data: { data: notice } });
  const { useAnnouncement } = await import("./useAnnouncement");
  const bar = useAnnouncement();
  await vi.waitFor(() => expect(bar.data.value).toEqual(notice));
  expect(get).toHaveBeenCalledWith("/api/system/announcement", { timeout: 5000 });
  bar.collapsed.value = true;
  const header = useAnnouncement();
  expect(header.collapsed.value).toBe(true);
  header.collapsed.value = false;
  expect(bar.collapsed.value).toBe(false);
  get.mockRejectedValueOnce({ response: { status: 404 } });
  await header.reload();
  expect(bar.data.value).toBeNull();
});

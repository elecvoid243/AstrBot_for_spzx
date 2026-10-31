// dashboard/src/composables/useSpcodeDirectoryBrowser.spec.ts
// Author: elecvoid243, 2026-08-15
//
// Unit tests for the directory-browser controller: listing/filtering,
// home/up navigation, root detection, breadcrumbs, truncation and error
// propagation. All API access goes through the mocked pluginExtensionApi
// (called as get(endpoint, { params: { path } })).

import { beforeEach, describe, expect, it, vi } from "vitest";

const getMock = vi.fn();
vi.mock("@/api/v1", () => ({
  pluginExtensionApi: {
    get: (...args: unknown[]) => getMock(...args),
    post: vi.fn(),
  },
}));

import {
  parentOf,
  buildBreadcrumbs,
  useSpcodeDirectoryBrowser,
} from "./useSpcodeDirectoryBrowser";

type Browser = ReturnType<typeof useSpcodeDirectoryBrowser>;

function withSetup<T>(fn: () => T): T {
  // The composable only creates refs/functions (no component-context deps),
  // so it can be invoked directly without a mounted component.
  return fn();
}

function dirEntry(name: string, path: string): Record<string, unknown> {
  return {
    path,
    name,
    type: "directory",
    size: null,
    mtime: 0,
    is_symlink: false,
  };
}
function fileEntry(name: string, path: string): Record<string, unknown> {
  return { path, name, type: "file", size: 10, mtime: 0, is_symlink: false };
}
function dirResponse(
  path: string,
  entries: Record<string, unknown>[],
  extra: Partial<{ truncated: boolean }> = {},
) {
  return {
    data: {
      status: "ok",
      data: {
        type: "directory",
        path,
        entry_count: entries.length,
        truncated: extra.truncated ?? false,
        max_entries: 1000,
        reason: null,
        elapsed_ms: 1,
        entries,
      },
    },
  };
}

/**
 * Route the mocked pluginExtensionApi.get by endpoint, extracting the real
 * path from config.params.path for file-browser calls.
 */
function mockApi(
  listing: (path: string) => unknown,
  home = "/home/user",
  drivesList: string[] = ["C:\\"],
) {
  getMock.mockImplementation(
    (endpoint: string, config?: { params?: { path?: string } }) => {
      if (endpoint === "spcode/home-directory") {
        return Promise.resolve({ data: { data: { home } } });
      }
      if (endpoint === "spcode/drives") {
        return Promise.resolve({ data: { data: { drives: drivesList } } });
      }
      const path = config?.params?.path ?? "";
      return Promise.resolve(listing(path));
    },
  );
}

beforeEach(() => {
  getMock.mockReset();
});

describe("useSpcodeDirectoryBrowser navigation", () => {
  it("goHome fetches home-directory then lists it, keeping only directories", async () => {
    mockApi(() =>
      dirResponse("/home/user", [
        dirEntry("proj", "/home/user/proj"),
        fileEntry("notes.txt", "/home/user/notes.txt"),
        { ...dirEntry("link", "/home/user/link"), type: "symlink" },
      ]),
    );
    const result = withSetup(() => useSpcodeDirectoryBrowser());
    await result.goHome();
    expect(result.currentPath.value).toBe("/home/user");
    expect(result.entries.value).toHaveLength(1);
    expect(result.entries.value[0].name).toBe("proj");
    expect(result.error.value).toBeNull();
  });

  it("goUp navigates to the parent; at a drive root opens the drive list", async () => {
    mockApi(
      (path) => dirResponse(path, [dirEntry("sub", `${path}\\sub`)]),
      "C:\\Users",
      ["C:\\", "D:\\"],
    );
    const result = withSetup(() => useSpcodeDirectoryBrowser());
    await result.goHome();
    expect(result.currentPath.value).toBe("C:\\Users");
    // Not a root yet → goUp moves to the drive root "C:\".
    await result.goUp();
    expect(result.currentPath.value).toBe("C:\\");
    expect(result.isAtRoot.value).toBe(true);
    expect(result.canGoUp.value).toBe(true);
    // At a Windows drive root, goUp opens the "This PC" drive list.
    await result.goUp();
    expect(result.computerMode.value).toBe(true);
    expect(result.drives.value).toEqual(["C:\\", "D:\\"]);
    // In This-PC mode goUp is a no-op.
    await result.goUp();
    expect(result.computerMode.value).toBe(true);
  });

  it("goUp is a no-op at the POSIX root", async () => {
    mockApi((path) => dirResponse(path, []), "/");
    const result = withSetup(() => useSpcodeDirectoryBrowser());
    await result.goHome();
    expect(result.currentPath.value).toBe("/");
    expect(result.canGoUp.value).toBe(false);
    await result.goUp();
    expect(result.currentPath.value).toBe("/");
    expect(result.computerMode.value).toBe(false);
  });

  it("openComputer fetches the drive list and clears the folder view", async () => {
    mockApi((path) => dirResponse(path, []), "C:\\Users", [
      "C:\\",
      "D:\\",
      "E:\\",
    ]);
    const result = withSetup(() => useSpcodeDirectoryBrowser());
    await result.goHome();
    await result.openComputer();
    expect(result.computerMode.value).toBe(true);
    expect(result.drives.value).toEqual(["C:\\", "D:\\", "E:\\"]);
    expect(result.entries.value).toHaveLength(0);
  });

  it("isAtRoot detects POSIX root and Windows drive roots", () => {
    const result = withSetup(() => useSpcodeDirectoryBrowser());
    result.currentPath.value = "/";
    expect(result.isAtRoot.value).toBe(true);
    result.currentPath.value = "C:/";
    expect(result.isAtRoot.value).toBe(true);
    result.currentPath.value = "D:\\";
    expect(result.isAtRoot.value).toBe(true);
    result.currentPath.value = "/home";
    expect(result.isAtRoot.value).toBe(false);
  });

  it("builds Windows breadcrumbs with cumulative paths", async () => {
    mockApi((path) => dirResponse(path, []));
    const result = withSetup(() => useSpcodeDirectoryBrowser());
    await result.list("C:\\Users\\foo");
    const crumbs = result.breadcrumbs.value;
    expect(crumbs.map((c) => c.label)).toEqual(["C:\\", "Users", "foo"]);
    expect(crumbs[2].path).toBe("C:\\Users\\foo\\");
  });

  it("builds POSIX breadcrumbs root-first", async () => {
    mockApi((path) => dirResponse(path, []));
    const result = withSetup(() => useSpcodeDirectoryBrowser());
    await result.list("/home/foo");
    const crumbs = result.breadcrumbs.value;
    expect(crumbs.map((c) => c.label)).toEqual(["/", "home", "foo"]);
    expect(crumbs[0].path).toBe("/");
    expect(crumbs[2].path).toBe("/home/foo");
  });

  it("propagates a backend error reason", async () => {
    mockApi(() => ({
      data: { data: { type: null, path: "/x", reason: "path_not_found" } },
    }));
    const result = withSetup(() => useSpcodeDirectoryBrowser());
    await result.list("/x");
    expect(result.error.value).toBe("path_not_found");
    expect(result.entries.value).toHaveLength(0);
  });

  it("sets the truncated flag from the listing", async () => {
    mockApi(() =>
      dirResponse("/big", [dirEntry("a", "/big/a")], { truncated: true }),
    );
    const result = withSetup(() => useSpcodeDirectoryBrowser());
    await result.list("/big");
    expect(result.truncated.value).toBe(true);
  });

  it("openEntry only enters directories", async () => {
    mockApi((path) => dirResponse(path, [dirEntry("child", `${path}/child`)]));
    const result = withSetup(() => useSpcodeDirectoryBrowser());
    await result.list("/base");
    await result.openEntry(result.entries.value[0]);
    expect(result.currentPath.value).toBe("/base/child");
  });
});

describe("host picker lifecycle", () => {
  it("keeps Windows roots absolute and renders the POSIX root", () => {
    expect(parentOf("C:/file.txt")).toBe("C:/");
    expect(parentOf("C:/")).toBeNull();
    expect(buildBreadcrumbs("/")).toEqual([{ label: "/", path: "/" }]);
  });

  it("ignores a late folder result after switching to drives", async () => {
    let resolve!: (value: unknown) => void;
    getMock.mockImplementation((route: string) =>
      route === "spcode/drives"
        ? Promise.resolve({ data: { data: { drives: ["C:/", "G:/"] } } })
        : new Promise((done) => {
            resolve = done;
          }),
    );
    const browser = useSpcodeDirectoryBrowser(true);
    const pending = browser.list("G:/slow");
    await browser.openComputer();
    resolve(dirResponse("G:/slow", []));
    await pending;
    expect(browser.computerMode.value).toBe(true);
    expect(browser.drives.value).toEqual(["C:/", "G:/"]);
    expect(getMock.mock.calls[0][1].params.scope).toBe("host");
  });

  it("does not apply home navigation after the picker closes", async () => {
    let resolve!: (value: unknown) => void;
    getMock.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const browser = useSpcodeDirectoryBrowser(true);
    const pending = browser.goHome();
    browser.cancel();
    resolve({ data: { data: { home: "C:/Users/example" } } });
    await pending;
    expect(getMock).toHaveBeenCalledTimes(1);
    expect(browser.loading.value).toBe(false);
  });
});

describe("host non-directory fallback", () => {
  it("still falls back to the parent of a legitimate file response", async () => {
    getMock.mockImplementation(
      async (_route: string, config: { params: { path: string } }) =>
        config.params.path.endsWith(".txt")
          ? {
              data: {
                status: "ok",
                data: {
                  type: "file",
                  path: config.params.path,
                  name: "file.txt",
                  content: "text",
                },
              },
            }
          : dirResponse(config.params.path, []),
    );
    const browser = useSpcodeDirectoryBrowser(true);
    await browser.list("C:/fixture/file.txt");
    expect(browser.currentPath.value).toBe("C:/fixture");
    expect(browser.error.value).toBeNull();
    expect(getMock.mock.calls.map((call) => call[1].params)).toEqual([
      { path: "C:/fixture/file.txt", scope: "host" },
      { path: "C:/fixture", scope: "host" },
    ]);
  });
});

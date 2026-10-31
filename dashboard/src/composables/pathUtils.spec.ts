// Author: elecvoid243, 2026-07-20
// Tests for the pure-TS path helpers in pathUtils.ts.
// No Vue / DOM — runs under node:test via vitest.

import { describe, expect, it } from "vitest";
import {
  projectRelativePath,
  projectRelativePathStrict,
  docsRootRelativePath,
  projectRelativeFromDoc,
  absoluteFromSelectedDoc,
  isPathInDocsRoot,
} from "./pathUtils";

describe("projectRelativePathStrict", () => {
  it.each([
    ["/tmp/A/same.py", "/tmp/A", "same.py"],
    ["/tmp/A/same.py", "/tmp/a", null],
    ["/tmp/A-old/same.py", "/tmp/A", null],
    ["/tmp/A/../B/same.py", "/tmp/A", null],
    ["/tmp/A/same.py", "/tmp/A/../A", null],
    ["/tmp/A", "/tmp/A/", ""],
    ["/tmp/A/same.py", "/", "tmp/A/same.py"],
    ["f:\\repo\\Same.py", "F:/Repo", "Same.py"],
    ["F:/Repo-old/Same.py", "f:/repo", null],
    ["//SERVER/share/Repo/Same.py", "\\\\server\\SHARE\\repo", "Same.py"],
    ["same.py", "/tmp/A", null],
    ["/tmp/A/same.py", null, null],
  ])(
    "resolves %s against %s without a basename fallback",
    (path, root, expected) => {
      expect(projectRelativePathStrict(path, root)).toBe(expected);
    },
  );
});

describe("isPathInDocsRoot", () => {
  it("returns false when projectRoot is null / undefined / empty", () => {
    expect(isPathInDocsRoot("/repo/docs/a.md", null, "docs")).toBe(false);
    expect(isPathInDocsRoot("/repo/docs/a.md", undefined, "docs")).toBe(false);
    expect(isPathInDocsRoot("/repo/docs/a.md", "", "docs")).toBe(false);
  });

  it("matches a file directly under the docs subtree (POSIX)", () => {
    expect(isPathInDocsRoot("/repo/docs/a.md", "/repo", "docs")).toBe(true);
  });

  it("matches a file nested in a subdirectory of the docs subtree (POSIX)", () => {
    expect(
      isPathInDocsRoot("/repo/docs/superpowers/spec.md", "/repo", "docs"),
    ).toBe(true);
  });

  it("rejects a file outside the docs subtree (POSIX)", () => {
    expect(isPathInDocsRoot("/repo/src/feature.py", "/repo", "docs")).toBe(
      false,
    );
    expect(isPathInDocsRoot("/other/repo/docs/a.md", "/repo", "docs")).toBe(
      false,
    );
  });

  it("rejects a file with a same-prefix name but different subtree (POSIX)", () => {
    // "docs-old" must NOT match the "docs" subtree.
    expect(isPathInDocsRoot("/repo/docs-old/a.md", "/repo", "docs")).toBe(
      false,
    );
  });

  it("treats docsRoot='.' as 'docs subtree == project root' (POSIX)", () => {
    expect(isPathInDocsRoot("/repo/a.md", "/repo", ".")).toBe(true);
    expect(isPathInDocsRoot("/repo/src/a.md", "/repo", ".")).toBe(true);
    expect(isPathInDocsRoot("/other/a.md", "/repo", ".")).toBe(false);
  });

  it("treats empty docsRoot as 'docs subtree == project root' (POSIX)", () => {
    expect(isPathInDocsRoot("/repo/a.md", "/repo", "")).toBe(true);
    expect(isPathInDocsRoot("/repo/src/a.md", "/repo", "  ")).toBe(true);
  });

  it("matches nested docs roots (POSIX)", () => {
    expect(
      isPathInDocsRoot(
        "/repo/docs/superpowers/specs/a.md",
        "/repo",
        "docs/superpowers",
      ),
    ).toBe(true);
    expect(
      isPathInDocsRoot("/repo/docs/a.md", "/repo", "docs/superpowers"),
    ).toBe(false);
  });

  it("normalizes separator mismatch: Windows-backslash path vs POSIX-slash root", () => {
    expect(
      isPathInDocsRoot(
        "\\repo\\docs\\superpowers\\spec.md",
        "/repo",
        "docs/superpowers",
      ),
    ).toBe(true);
    expect(isPathInDocsRoot("\\repo\\src\\a.py", "/repo", "docs")).toBe(false);
  });

  it("normalizes separator mismatch: POSIX-slash path vs Windows-backslash root", () => {
    expect(
      isPathInDocsRoot("/repo/docs/a.md", "\\repo", "docs\\superpowers"),
    ).toBe(false);
    expect(
      isPathInDocsRoot(
        "/repo/docs/superpowers/spec.md",
        "\\repo",
        "docs\\superpowers",
      ),
    ).toBe(true);
  });

  it("matches case-insensitive on the leading segment (Windows-friendly)", () => {
    expect(isPathInDocsRoot("\\Repo\\Docs\\A.md", "\\repo", "docs")).toBe(true);
    expect(isPathInDocsRoot("\\REPO\\DOCS\\A.md", "\\repo", "docs")).toBe(true);
  });

  it("does not false-match when the trailing '/' is missing on the prefix (regression)", () => {
    // "F:/repo" is projectRoot; "F:/repository" must NOT match.
    expect(isPathInDocsRoot("/repository/docs/a.md", "/repo", "docs")).toBe(
      false,
    );
  });

  it("handles trailing slashes on docsRoot without breaking the match", () => {
    expect(isPathInDocsRoot("/repo/docs/a.md", "/repo", "docs/")).toBe(true);
    expect(
      isPathInDocsRoot(
        "/repo/docs/superpowers/a.md",
        "/repo",
        "docs/superpowers/",
      ),
    ).toBe(true);
  });
});

describe("projectRelativePath", () => {
  it("returns '' for the project root itself", () => {
    expect(projectRelativePath("/repo", "/repo")).toBe("");
  });
  it("strips the root and returns the project-relative remainder", () => {
    expect(projectRelativePath("/repo/docs/a.md", "/repo")).toBe("docs/a.md");
  });
  it("falls back to basename when the prefix does not match", () => {
    expect(projectRelativePath("/other/a.md", "/repo")).toBe("a.md");
  });
  it("matches case-insensitive on the leading segment", () => {
    expect(projectRelativePath("/Repo/a.md", "/repo")).toBe("a.md");
  });
});

describe("docsRootRelativePath", () => {
  it("returns the docsRoot-relative path (POSIX)", () => {
    expect(docsRootRelativePath("/repo/docs/a.md", "/repo", "docs")).toBe(
      "a.md",
    );
  });
  it("returns the docsRoot-relative path with nested docs (POSIX)", () => {
    expect(
      docsRootRelativePath(
        "/repo/docs/superpowers/spec.md",
        "/repo",
        "docs/superpowers",
      ),
    ).toBe("spec.md");
  });
  it("returns '' for the docsRoot itself", () => {
    expect(docsRootRelativePath("/repo/docs", "/repo", "docs")).toBe("");
  });
  it("falls back to basename when the prefix does not match", () => {
    expect(docsRootRelativePath("/repo/src/a.md", "/repo", "docs")).toBe(
      "a.md",
    );
  });
});

describe("projectRelativeFromDoc", () => {
  it("glues docsRoot + doc into a project-relative path", () => {
    expect(projectRelativeFromDoc("docs", "a.md")).toBe("docs/a.md");
  });
  it("returns doc unchanged when docsRoot is '.'", () => {
    expect(projectRelativeFromDoc(".", "a.md")).toBe("a.md");
  });
  it("returns doc unchanged when docsRoot is empty", () => {
    expect(projectRelativeFromDoc("", "a.md")).toBe("a.md");
  });
  it("strips leading slashes from the doc", () => {
    expect(projectRelativeFromDoc("docs", "/a.md")).toBe("docs/a.md");
  });
});

describe("absoluteFromSelectedDoc", () => {
  it("glues projectRoot + docsRoot + selectedDoc (POSIX)", () => {
    expect(absoluteFromSelectedDoc("/repo", "docs", "a.md")).toBe(
      "/repo/docs/a.md",
    );
  });
  it("drops the docs prefix when docsRoot is '.'", () => {
    expect(absoluteFromSelectedDoc("/repo", ".", "a.md")).toBe("/repo/a.md");
  });
  it("preserves the root's native separator (Windows)", () => {
    expect(absoluteFromSelectedDoc("\\repo", "docs", "a.md")).toBe(
      "\\repo\\docs\\a.md",
    );
  });
  it("normalizes POSIX '/' inside docsRoot/selectedDoc to match a Windows root", () => {
    expect(
      absoluteFromSelectedDoc("\\repo", "docs/superpowers", "specs/a.md"),
    ).toBe("\\repo\\docs\\superpowers\\specs\\a.md");
  });
});

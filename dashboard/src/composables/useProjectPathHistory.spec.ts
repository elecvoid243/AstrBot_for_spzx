import { afterEach, describe, expect, it } from "vitest";
import { useProjectPathHistory } from "./useProjectPathHistory";

afterEach(() => {
  localStorage.clear();
});

describe("project path history belongs to the account", () => {
  it("exposes the 10 most recent paths", () => {
    localStorage.setItem("role", "admin");
    const history = useProjectPathHistory();
    for (let i = 1; i <= 12; i += 1) {
      history.addToPathHistory(`/project/${i}`);
    }

    expect(history.recentPaths.value).toHaveLength(10);
    expect(history.recentPaths.value[0]).toBe("/project/12");
    expect(history.recentPaths.value[9]).toBe("/project/3");
  });

  it("keeps recent paths separate when switching accounts in the same browser", () => {
    localStorage.setItem("role", "user");
    localStorage.setItem("account_id", "first");
    const first = useProjectPathHistory();
    first.addToPathHistory("/first/workspace");
    window.dispatchEvent(new Event("astrbot:account-changed"));
    expect(first.recentPaths.value).toEqual([]);
    localStorage.setItem("account_id", "second");
    const second = useProjectPathHistory();
    expect(second.recentPaths.value).toEqual([]);
    second.addToPathHistory("/second/workspace");
    localStorage.setItem("account_id", "first");
    expect(useProjectPathHistory().recentPaths.value).toEqual([
      "/first/workspace",
    ]);
  });

  it("retains legacy administrator paths without exposing them to ordinary users", () => {
    localStorage.setItem(
      "chatui.spcode.projectPathHistory",
      JSON.stringify(["/administrator/project"]),
    );
    localStorage.setItem("account_id", "ordinary");
    localStorage.setItem("role", "user");
    expect(useProjectPathHistory().recentPaths.value).toEqual([]);
    localStorage.setItem("account_id", "administrator");
    localStorage.setItem("role", "admin");
    expect(useProjectPathHistory().recentPaths.value).toEqual([
      "/administrator/project",
    ]);
  });
});

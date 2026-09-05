// Unit tests for the agent-teams directory composable: envelope wiring,
// list refresh after create, selection, workflow save/delete routing and
// error toasting (error envelopes and network failures never throw).
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  listTeams: vi.fn(),
  createTeam: vi.fn(),
  updateTeam: vi.fn(),
  deleteTeam: vi.fn(),
  listWorkflows: vi.fn(),
  createWorkflow: vi.fn(),
  updateWorkflow: vi.fn(),
  deleteWorkflow: vi.fn(),
}));

vi.mock("@/api/v1", () => ({ agentTeamsApi: apiMocks }));

const toastMocks = vi.hoisted(() => ({
  toast: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}));

vi.mock("@/utils/toast", () => ({ useToast: () => toastMocks }));

import { useAgentTeams } from "./useAgentTeams";

const ok = (data: unknown) =>
  Promise.resolve({ data: { status: "ok", message: null, data } });
const fail = (message: string) =>
  Promise.resolve({ data: { status: "error", message, data: null } });

const TEAM_A = { team_id: "t1", name: "Alpha", members: [] };
const TEAM_B = { team_id: "t2", name: "Beta", members: [] };
const WORKFLOW = { workflow_id: "w1", name: "daily" };

beforeEach(async () => {
  for (const mock of Object.values(apiMocks)) mock.mockReset();
  for (const mock of Object.values(toastMocks)) mock.mockReset();
  // Reset the module-singleton refs through the composable itself.
  apiMocks.listTeams.mockResolvedValue(ok({ teams: [] }));
  apiMocks.listWorkflows.mockResolvedValue(ok({ workflows: [] }));
  const api = useAgentTeams();
  api.selectTeam(null);
  await Promise.all([api.loadTeams(), api.loadWorkflows("-")]);
});

describe("useAgentTeams", () => {
  it("shares singleton state across composable calls", async () => {
    apiMocks.listTeams.mockResolvedValueOnce(ok({ teams: [TEAM_A] }));
    const a = useAgentTeams();
    await a.loadTeams();
    a.selectTeam("t1");
    const b = useAgentTeams();
    expect(b.teams.value.map((t) => t.team_id)).toEqual(["t1"]);
    expect(b.selectedTeamId.value).toBe("t1");
    expect(b.selectedTeam.value?.name).toBe("Alpha");
  });

  it("loadTeams populates teams from the envelope", async () => {
    apiMocks.listTeams.mockResolvedValueOnce(ok({ teams: [TEAM_A, TEAM_B] }));
    const api = useAgentTeams();
    const teams = await api.loadTeams();
    expect(teams).toHaveLength(2);
    expect(api.teams.value).toEqual([TEAM_A, TEAM_B]);
  });

  it("selectTeam(null) clears the selection", async () => {
    const api = useAgentTeams();
    apiMocks.listTeams.mockResolvedValueOnce(ok({ teams: [TEAM_A] }));
    await api.loadTeams();
    api.selectTeam("t1");
    expect(api.selectedTeam.value?.team_id).toBe("t1");
    api.selectTeam(null);
    expect(api.selectedTeam.value).toBeNull();
  });

  it("createTeam posts the payload and refreshes the list", async () => {
    const api = useAgentTeams();
    apiMocks.listTeams.mockResolvedValueOnce(ok({ teams: [TEAM_A] }));
    await api.loadTeams();
    apiMocks.createTeam.mockResolvedValueOnce(ok(TEAM_B));
    apiMocks.listTeams.mockResolvedValueOnce(ok({ teams: [TEAM_A, TEAM_B] }));
    const created = await api.createTeam({ name: "Beta", members: [] });
    expect(apiMocks.createTeam).toHaveBeenCalledWith({ name: "Beta", members: [] });
    expect(created).toEqual(TEAM_B);
    expect(api.teams.value.map((t) => t.team_id)).toEqual(["t1", "t2"]);
  });

  it("error envelopes toast and return null without mutating state", async () => {
    const api = useAgentTeams();
    apiMocks.listTeams.mockResolvedValueOnce(ok({ teams: [TEAM_A] }));
    await api.loadTeams();
    apiMocks.createTeam.mockResolvedValueOnce(fail("team name required"));
    const created = await api.createTeam({ name: "" });
    expect(created).toBeNull();
    expect(toastMocks.error).toHaveBeenCalledWith("team name required");
    expect(api.teams.value.map((t) => t.team_id)).toEqual(["t1"]);
  });

  it("network failures toast and never throw", async () => {
    const api = useAgentTeams();
    apiMocks.listTeams.mockRejectedValueOnce(new Error("network down"));
    await expect(api.loadTeams()).resolves.toBeNull();
    expect(toastMocks.error).toHaveBeenCalledWith("network down");
    expect(api.teams.value).toEqual([]);
  });

  it("HTTP error rejections toast the backend envelope message", async () => {
    // Non-2xx responses reach callers as axios rejections carrying the error
    // envelope body; its message must win over axios's generic
    // "Request failed with status code 400".
    apiMocks.createTeam.mockRejectedValueOnce({
      message: "Request failed with status code 400",
      response: {
        status: 400,
        data: { status: "error", message: "后端具体原因" },
      },
    });
    const api = useAgentTeams();
    expect(await api.createTeam({ name: "x", members: [] })).toBeNull();
    expect(toastMocks.error).toHaveBeenCalledWith("后端具体原因");
  });

  it("plain-string rejections (the 429 case) toast the string itself", async () => {
    apiMocks.listTeams.mockRejectedValueOnce("请求过于频繁");
    const api = useAgentTeams();
    expect(await api.loadTeams()).toBeNull();
    expect(toastMocks.error).toHaveBeenCalledWith("请求过于频繁");
  });

  it("updateTeam replaces the row in place", async () => {
    const api = useAgentTeams();
    apiMocks.listTeams.mockResolvedValueOnce(ok({ teams: [TEAM_A] }));
    await api.loadTeams();
    const updated = { ...TEAM_A, name: "Alpha2" };
    apiMocks.updateTeam.mockResolvedValueOnce(ok(updated));
    const result = await api.updateTeam("t1", { name: "Alpha2" });
    expect(apiMocks.updateTeam).toHaveBeenCalledWith("t1", { name: "Alpha2" });
    expect(result).toEqual(updated);
    expect(api.teams.value[0].name).toBe("Alpha2");
  });

  it("deleteTeam removes the row and clears a matching selection", async () => {
    const api = useAgentTeams();
    apiMocks.listTeams.mockResolvedValueOnce(ok({ teams: [TEAM_A, TEAM_B] }));
    await api.loadTeams();
    api.selectTeam("t1");
    apiMocks.deleteTeam.mockResolvedValueOnce(ok({ message: "deleted" }));
    expect(await api.deleteTeam("t1")).toEqual({ message: "deleted" });
    expect(api.teams.value.map((t) => t.team_id)).toEqual(["t2"]);
    expect(api.selectedTeam.value).toBeNull();
  });

  it("loadWorkflows populates workflows from the envelope", async () => {
    const api = useAgentTeams();
    apiMocks.listWorkflows.mockResolvedValueOnce(ok({ workflows: [WORKFLOW] }));
    const workflows = await api.loadWorkflows("t1");
    expect(apiMocks.listWorkflows).toHaveBeenCalledWith("t1");
    expect(workflows).toEqual([WORKFLOW]);
  });

  it("saveWorkflow creates when workflow_id is absent and refreshes the list", async () => {
    const api = useAgentTeams();
    apiMocks.createWorkflow.mockResolvedValueOnce(ok(WORKFLOW));
    apiMocks.listWorkflows.mockResolvedValueOnce(ok({ workflows: [WORKFLOW] }));
    const saved = await api.saveWorkflow("t1", { name: "daily" });
    expect(apiMocks.createWorkflow).toHaveBeenCalledWith("t1", { name: "daily" });
    expect(apiMocks.updateWorkflow).not.toHaveBeenCalled();
    expect(saved).toEqual(WORKFLOW);
    expect(api.workflows.value).toEqual([WORKFLOW]);
  });

  it("saveWorkflow updates when workflow_id is present", async () => {
    const api = useAgentTeams();
    const payload = { workflow_id: "w1", name: "daily2" };
    apiMocks.updateWorkflow.mockResolvedValueOnce(ok({ ...WORKFLOW, name: "daily2" }));
    apiMocks.listWorkflows.mockResolvedValueOnce(
      ok({ workflows: [{ ...WORKFLOW, name: "daily2" }] }),
    );
    const saved = await api.saveWorkflow("t1", payload);
    expect(apiMocks.updateWorkflow).toHaveBeenCalledWith("t1", "w1", payload);
    expect(apiMocks.createWorkflow).not.toHaveBeenCalled();
    expect(saved).toEqual({ ...WORKFLOW, name: "daily2" });
    expect(api.workflows.value[0].name).toBe("daily2");
  });

  it("deleteWorkflow removes it from the workflows list", async () => {
    const api = useAgentTeams();
    apiMocks.listWorkflows.mockResolvedValueOnce(ok({ workflows: [WORKFLOW] }));
    await api.loadWorkflows("t1");
    apiMocks.deleteWorkflow.mockResolvedValueOnce(ok({ message: "deleted" }));
    expect(await api.deleteWorkflow("t1", "w1")).toEqual({ message: "deleted" });
    expect(apiMocks.deleteWorkflow).toHaveBeenCalledWith("t1", "w1");
    expect(api.workflows.value).toEqual([]);
  });

  it("workflow error envelopes toast", async () => {
    const api = useAgentTeams();
    apiMocks.deleteWorkflow.mockResolvedValueOnce(fail("workflow in use"));
    expect(await api.deleteWorkflow("t1", "w1")).toBeNull();
    expect(toastMocks.error).toHaveBeenCalledWith("workflow in use");
  });

  it("captures structured field errors from HTTP rejections and clears on success", async () => {
    const api = useAgentTeams();
    apiMocks.createWorkflow.mockRejectedValueOnce({
      message: "Request failed with status code 400",
      response: {
        status: 400,
        data: {
          status: "error",
          message: "工作流校验失败",
          data: {
            fields: [
              {
                path: "nodes.n2.execution.config_id",
                message: "节点 n2 的配置档案不存在: xxx",
              },
            ],
          },
        },
      },
    });
    expect(await api.saveWorkflow("t1", { name: "x" })).toBeNull();
    expect(toastMocks.error).toHaveBeenCalledWith("工作流校验失败");
    expect(api.lastErrorFields.value).toEqual([
      {
        path: "nodes.n2.execution.config_id",
        message: "节点 n2 的配置档案不存在: xxx",
      },
    ]);

    // The next (successful) call resets the fields.
    apiMocks.createWorkflow.mockResolvedValueOnce(ok(WORKFLOW));
    apiMocks.listWorkflows.mockResolvedValueOnce(ok({ workflows: [WORKFLOW] }));
    await api.saveWorkflow("t1", { name: "x" });
    expect(api.lastErrorFields.value).toEqual([]);
  });

  it("keeps lastErrorFields empty for plain failures without field data", async () => {
    const api = useAgentTeams();
    apiMocks.listTeams.mockRejectedValueOnce(new Error("network down"));
    expect(await api.loadTeams()).toBeNull();
    expect(api.lastErrorFields.value).toEqual([]);
  });
});

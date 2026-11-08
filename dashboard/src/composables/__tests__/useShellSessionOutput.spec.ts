// Peek-polling loop for floating shell session output windows.
// Client-side cursor from 0; never consumes the agent's cursor (the backend
// peek endpoint guarantees that — these tests pin the client half).
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/remoteConnections", () => ({remoteConnectionsApi:{shellOutput:vi.fn()}}));

vi.mock("@/api/v1", () => ({
  chatApi: {
    getShellSessionOutput: vi.fn(),
  },
}));

import { remoteConnectionsApi } from "@/api/remoteConnections";
import { chatApi } from "@/api/v1";
import { useShellSessionOutput } from "../useShellSessionOutput";
import { resetAccountSession } from "@/utils/accountSession";

const getOutput = vi.mocked(chatApi.getShellSessionOutput);

it("removes retained output and discards an in-flight response on account switch", async () => {
  let finish!: (value: unknown) => void;
  getOutput.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }) as any);
  const output = useShellSessionOutput("private-session", "private-shell");
  output.outputText.value = "old private output";
  const pending = output.start();
  resetAccountSession(false);
  finish(peek("late private output", 19));
  await pending;
  expect(output.outputText.value).toBe("");
  expect(output.sessionClosed.value).toBe(true);
});

function peek(
  stdout: string,
  cursor: number,
  overrides: Record<string, unknown> = {},
) {
  return {
    data: {
      data: {
        session_id: "sh_1",
        pid: 1,
        status: "running",
        stdout,
        stderr: "",
        exit_code: null,
        cursor,
        has_more: false,
        session_closed: false,
        ...overrides,
      },
    },
  };
}

describe("useShellSessionOutput", () => {
  beforeEach(() => {
    getOutput.mockReset();
  });

  it("appends incremental output and advances its own cursor", async () => {
    getOutput
      .mockResolvedValueOnce(peek("a", 1) as never)
      .mockResolvedValueOnce(peek("b", 2) as never)
      .mockResolvedValueOnce(peek("", 2, { session_closed: true, status: "completed", exit_code: 0 }) as never);

    const out = useShellSessionOutput("s1", "sh_1");
    await out.start();

    expect(out.outputText.value).toBe("ab");
    expect(out.sessionClosed.value).toBe(true);
    expect(out.status.value).toBe("completed");
    const cursors = getOutput.mock.calls.map((c) => c[2]?.cursor);
    expect(cursors).toEqual([0, 1, 2]);
  });

  it("stops polling once the session is closed", async () => {
    getOutput.mockResolvedValueOnce(
      peek("x", 1, { session_closed: true, status: "terminated" }) as never,
    );

    const out = useShellSessionOutput("s1", "sh_1");
    await out.start();

    expect(getOutput).toHaveBeenCalledTimes(1);
    expect(out.outputText.value).toBe("x");
  });

  it("keeps displayed content when the session vanishes mid-follow", async () => {
    // AstrBot business errors arrive as HTTP 200 with {status:"error"}
    // envelopes — axios does NOT reject, so the loop must branch on it.
    getOutput
      .mockResolvedValueOnce(peek("partial\n", 8) as never)
      .mockResolvedValueOnce({
        data: { status: "error", message: "Shell session sh_1 was not found." },
      } as never);

    const out = useShellSessionOutput("s1", "sh_1");
    await out.start();

    expect(out.outputText.value).toBe("partial\n");
    expect(out.sessionClosed.value).toBe(true);
  });

  it("keeps displayed content on network failure", async () => {
    getOutput
      .mockResolvedValueOnce(peek("partial\n", 8) as never)
      .mockRejectedValueOnce(new Error("network down"));

    const out = useShellSessionOutput("s1", "sh_1");
    await out.start();

    expect(out.outputText.value).toBe("partial\n");
    expect(out.sessionClosed.value).toBe(true);
  });

  it("stop() prevents further requests after the in-flight one", async () => {
    let resolveFirst: (v: unknown) => void;
    getOutput
      .mockReturnValueOnce(new Promise((r) => (resolveFirst = r)) as never)
      .mockResolvedValue(peek("never", 2) as never);

    const out = useShellSessionOutput("s1", "sh_1");
    const done = out.start();
    out.stop();
    resolveFirst!(peek("a", 1, { session_closed: false }) as never);
    await done;

    expect(getOutput).toHaveBeenCalledTimes(1);
    expect(out.outputText.value).toBe("a");
  });
});


it("uses a binding-scoped independent output cursor and clears remote output on grant failure", async () => {
  const scope={connectionId:"machine",sessionId:"remote",revision:"key-v1"};
  vi.mocked(remoteConnectionsApi.shellOutput).mockResolvedValueOnce(peek("remote log",10) as any).mockRejectedValueOnce(new Error("Grant revoked"));
  const output=useShellSessionOutput("remote","remote-process",scope);
  await output.start();
  expect(remoteConnectionsApi.shellOutput).toHaveBeenNthCalledWith(1,scope,"remote-process",0);
  expect(remoteConnectionsApi.shellOutput).toHaveBeenNthCalledWith(2,scope,"remote-process",10);
  expect(output.outputText.value).toBe("");
  expect(output.sessionClosed.value).toBe(true);
});

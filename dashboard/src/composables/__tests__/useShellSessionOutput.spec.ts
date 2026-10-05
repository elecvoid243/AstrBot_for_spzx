// Peek-polling loop for floating shell session output windows.
// Client-side cursor from 0; never consumes the agent's cursor (the backend
// peek endpoint guarantees that — these tests pin the client half).
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/v1", () => ({
  chatApi: {
    getShellSessionOutput: vi.fn(),
  },
}));

import { chatApi } from "@/api/v1";
import { useShellSessionOutput } from "../useShellSessionOutput";

const getOutput = vi.mocked(chatApi.getShellSessionOutput);

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
    getOutput
      .mockResolvedValueOnce(peek("partial\n", 8) as never)
      .mockRejectedValueOnce(new Error("Shell session sh_1 was not found."));

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

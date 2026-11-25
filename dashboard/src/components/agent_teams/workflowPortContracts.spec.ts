import { describe, expect, it } from "vitest";
import cases from "../../../../tests/agent_teams/fixtures/port_domain_cases.json";
import { incompatiblePortShapes } from "./workflowPortContracts";

describe("declared port domains", () => {
  it.each(cases)("$name", ({ source, target, compatible }) => {
    expect(incompatiblePortShapes(source, target)).toBe(!compatible);
  });
});

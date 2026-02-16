import { describe, it, expect } from "vitest";
import { createOrchestratorServer } from "../servers/orchestrator.js";

describe("createOrchestratorServer", () => {
  it("returns an MCP server object", () => {
    const server = createOrchestratorServer();
    expect(server).toBeDefined();
    expect(server).toHaveProperty("name", "orchestrator");
    expect(server).toHaveProperty("type", "sdk");
    expect(server).toHaveProperty("instance");
  });
});

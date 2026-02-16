import { describe, it, expect } from "vitest";
import { callPython } from "../bridge.js";

describe("callPython", () => {
  it("calls a sync Python function and returns parsed JSON", async () => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "list_strategies",
      {}
    );
    expect(Array.isArray(result)).toBe(true);
    expect(result).toContain("skilled_traders");
  });

  it("reads a JSON file via bridge", async () => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "read_json_file",
      { path: "strategies/skilled_traders/exploration_tree.json" }
    );
    expect(result).toHaveProperty("strategy_name");
    expect((result as Record<string, unknown>).strategy_name).toBe(
      "skilled_traders"
    );
  });

  it("throws on missing module", async () => {
    await expect(
      callPython("nonexistent_xyz", "foo", {})
    ).rejects.toThrow();
  });

  it("throws on missing function", async () => {
    await expect(
      callPython("json", "nonexistent_xyz", {})
    ).rejects.toThrow();
  });

  it("respects timeout", async () => {
    // _sleep(seconds=10) should exceed a 2s timeout
    await expect(
      callPython(
        "polymarket_pipeline.cli.bridge",
        "_sleep",
        { seconds: 10 },
        { timeoutMs: 2000 }
      )
    ).rejects.toThrow(/timeout|timed out/i);
  }, 10000);
});

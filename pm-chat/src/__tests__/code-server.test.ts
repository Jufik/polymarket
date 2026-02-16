import { describe, it, expect } from "vitest";
import { createCodeServer } from "../servers/code.js";
import { ToolRegistry } from "../tools/registry.js";

describe("ToolRegistry", () => {
  it("starts empty", () => {
    const registry = new ToolRegistry();
    expect(registry.getTools()).toEqual([]);
  });

  it("registers a tool and returns it", () => {
    const registry = new ToolRegistry();
    registry.register({
      name: "test_tool",
      description: "A test tool",
      inputSchema: { value: { type: "string" } },
      pythonHandler: "path/to/handler.py",
    });
    expect(registry.getTools()).toHaveLength(1);
  });

  it("prevents duplicate tool names", () => {
    const registry = new ToolRegistry();
    registry.register({
      name: "test_tool",
      description: "A test tool",
      inputSchema: { value: { type: "string" } },
      pythonHandler: "path/to/handler.py",
    });
    expect(() =>
      registry.register({
        name: "test_tool",
        description: "Duplicate",
        inputSchema: {},
        pythonHandler: "path/to/other.py",
      })
    ).toThrow(/already registered/);
  });

  it("serializes for persistence", () => {
    const registry = new ToolRegistry();
    registry.register({
      name: "test_tool",
      description: "A test tool",
      inputSchema: { value: { type: "string" } },
      pythonHandler: "path/to/handler.py",
    });
    const serialized = registry.serialize();
    expect(serialized).toHaveLength(1);
    expect(serialized[0].name).toBe("test_tool");
    expect(serialized[0].pythonHandler).toBe("path/to/handler.py");
  });

  it("restores from serialized data", () => {
    const registry = new ToolRegistry();
    registry.loadFrom([
      {
        name: "restored_tool",
        description: "Restored",
        inputSchema: { x: { type: "number" } },
        pythonHandler: "path/to/restored.py",
      },
    ]);
    expect(registry.getTools()).toHaveLength(1);
    const serialized = registry.serialize();
    expect(serialized[0].name).toBe("restored_tool");
  });

  it("clears existing tools on loadFrom", () => {
    const registry = new ToolRegistry();
    registry.register({
      name: "old_tool",
      description: "Old",
      inputSchema: {},
      pythonHandler: "old.py",
    });
    registry.loadFrom([
      {
        name: "new_tool",
        description: "New",
        inputSchema: {},
        pythonHandler: "new.py",
      },
    ]);
    expect(registry.getTools()).toHaveLength(1);
    expect(registry.serialize()[0].name).toBe("new_tool");
  });
});

describe("createCodeServer", () => {
  it("returns an MCP server object", () => {
    const registry = new ToolRegistry();
    const server = createCodeServer(registry);
    expect(server).toBeDefined();
    expect(server).toHaveProperty("name", "code");
    expect(server).toHaveProperty("type", "sdk");
    expect(server).toHaveProperty("instance");
  });
});

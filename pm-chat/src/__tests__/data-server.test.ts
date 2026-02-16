import { describe, it, expect } from "vitest";
import { createDataServer, SQL_MUTATION_PATTERN } from "../servers/data.js";

describe("SQL_MUTATION_PATTERN", () => {
  it("blocks INSERT statements", () => {
    expect(SQL_MUTATION_PATTERN.test("INSERT INTO foo VALUES (1)")).toBe(true);
  });
  it("blocks DROP statements", () => {
    expect(SQL_MUTATION_PATTERN.test("DROP TABLE foo")).toBe(true);
  });
  it("blocks DELETE statements", () => {
    expect(SQL_MUTATION_PATTERN.test("DELETE FROM foo WHERE id = 1")).toBe(true);
  });
  it("blocks ALTER statements", () => {
    expect(SQL_MUTATION_PATTERN.test("ALTER TABLE foo ADD COLUMN bar TEXT")).toBe(
      true
    );
  });
  it("blocks CREATE statements", () => {
    expect(SQL_MUTATION_PATTERN.test("CREATE TABLE foo (id INT)")).toBe(true);
  });
  it("blocks TRUNCATE statements", () => {
    expect(SQL_MUTATION_PATTERN.test("TRUNCATE TABLE foo")).toBe(true);
  });
  it("blocks UPDATE statements", () => {
    expect(SQL_MUTATION_PATTERN.test("UPDATE foo SET bar = 1")).toBe(true);
  });
  it("blocks REPLACE statements", () => {
    expect(SQL_MUTATION_PATTERN.test("REPLACE INTO foo VALUES (1)")).toBe(true);
  });
  it("allows SELECT statements", () => {
    expect(SQL_MUTATION_PATTERN.test("SELECT * FROM foo")).toBe(false);
  });
  it("allows SHOW statements", () => {
    expect(SQL_MUTATION_PATTERN.test("SHOW CREATE TABLE foo")).toBe(false);
  });
  it("allows DESCRIBE statements", () => {
    expect(SQL_MUTATION_PATTERN.test("DESCRIBE foo")).toBe(false);
  });
  it("blocks case-insensitive", () => {
    expect(SQL_MUTATION_PATTERN.test("insert into foo values (1)")).toBe(true);
    expect(SQL_MUTATION_PATTERN.test("Drop Table foo")).toBe(true);
  });
});

describe("createDataServer", () => {
  it("returns an MCP server object", () => {
    const server = createDataServer();
    expect(server).toBeDefined();
    expect(server).toHaveProperty("name", "data");
    expect(server).toHaveProperty("type", "sdk");
    expect(server).toHaveProperty("instance");
  });
});

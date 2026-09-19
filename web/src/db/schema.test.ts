import { describe, expect, it } from "vitest";
import { getTableConfig } from "drizzle-orm/pg-core";
import * as schema from "./schema";

describe("introspected schema", () => {
  it("includes the chat history tables", () => {
    expect(schema).toHaveProperty("conversations");
    expect(schema).toHaveProperty("messages");
  });

  it("keys conversations on the provider-prefixed sub", () => {
    const cols = Object.keys((schema.conversations as any)[Symbol.for("drizzle:Columns")]);
    expect(cols).toContain("userSub");
  });
});

const RLS_TABLES = [
  "conversations", "messages", "turn_run", "llm_calls", "tool_calls",
  "message_feedback", "user_profile", "user_memory", "memory_outbox",
] as const;

describe("schema.ts is the migration source (Phase 16 Task 6)", () => {
  it("no longer models alembic_version", () => {
    expect((schema as Record<string, unknown>).alembicVersion).toBeUndefined();
  });

  it("declares the three roles as pre-existing", () => {
    for (const r of [schema.poseidonApp, schema.poseidonAdmin, schema.poseidonWorker]) {
      expect(r).toBeDefined();
    }
  });

  it.each(RLS_TABLES)("%s has RLS enabled and an owner policy with both predicates", (name) => {
    const table = Object.values(schema).find(
      (t) => typeof t === "object" && t !== null && "enableRLS" in (t as object) && getTableConfig(t as never).name === name,
    );
    expect(table, name).toBeDefined();
    const cfg = getTableConfig(table as never);
    expect(cfg.enableRLS).toBe(true);
    const owner = cfg.policies.find((p) => p.name === `${name}_owner`);
    expect(owner, `${name}_owner`).toBeDefined();
    expect(owner!.using).toBeDefined();
    expect(owner!.withCheck).toBeDefined();
  });

  it("carries the four admin read policies and the worker policy", () => {
    const names = Object.values(schema)
      .filter((t) => typeof t === "object" && t !== null && "enableRLS" in (t as object))
      .flatMap((t) => getTableConfig(t as never).policies.map((p) => p.name));
    for (const n of ["turn_run_admin_read", "llm_calls_admin_read", "tool_calls_admin_read", "message_feedback_admin_read", "memory_outbox_worker"]) {
      expect(names).toContain(n);
    }
    expect(names.filter((n) => n.endsWith("_owner"))).toHaveLength(9);
  });

  it("indexes carry no operator classes", () => {
    for (const t of Object.values(schema)) {
      if (typeof t !== "object" || t === null || !("enableRLS" in (t as object))) continue;
      for (const idx of getTableConfig(t as never).indexes) {
        for (const col of idx.config.columns) {
          expect((col as { op?: string }).op ?? (col as { indexConfig?: { opClass?: string } }).indexConfig?.opClass).toBeUndefined();
        }
      }
    }
  });
});

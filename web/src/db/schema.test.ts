import { describe, expect, it } from "vitest";
import { getTableConfig, PgDialect } from "drizzle-orm/pg-core";
import type { SQL } from "drizzle-orm";
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

// Renders a policy predicate's SQL text without a live database connection --
// PgDialect.sqlToQuery(sql`true`).sql === "true" -- so `using`/`withCheck` can
// be pinned on their actual rendered text rather than on object identity
// (which only OWNER_PREDICATE, reused verbatim by every owner policy, can
// use; the ad hoc `sql\`true\`` literals below are distinct SQL objects each
// time, so identity would fail even when the text is identical).
const dialect = new PgDialect();
const renderSql = (value: SQL | undefined): string | undefined =>
  value === undefined ? undefined : dialect.sqlToQuery(value).sql;

const isRlsTable = (t: unknown): t is object =>
  typeof t === "object" && t !== null && "enableRLS" in (t as object);

const findTable = (name: string) =>
  Object.values(schema).find((t) => isRlsTable(t) && getTableConfig(t as never).name === name);

const ROLES = {
  poseidonAdmin: schema.poseidonAdmin,
  poseidonWorker: schema.poseidonWorker,
} as const;

describe("schema.ts is the migration source (Phase 16 Task 6)", () => {
  it("no longer models alembic_version", () => {
    expect((schema as Record<string, unknown>).alembicVersion).toBeUndefined();
  });

  it("declares the three roles as pre-existing, with their exact cluster names", () => {
    const roles: Array<[unknown, string]> = [
      [schema.poseidonApp, "poseidon_app"],
      [schema.poseidonAdmin, "poseidon_admin"],
      [schema.poseidonWorker, "poseidon_worker"],
    ];
    for (const [role, name] of roles) {
      expect(role, name).toBeDefined();
      expect((role as { name: string }).name).toBe(name);
      // `_existing` is `@internal` in drizzle-orm's PgRole (roles.js) and not
      // part of the public .d.ts -- there is no other way to assert
      // `.existing()` took effect, so this reads the internal field directly.
      expect((role as unknown as { _existing?: boolean })._existing).toBe(true);
    }
  });

  it.each(RLS_TABLES)("%s has RLS enabled and an owner policy pinned to OWNER_PREDICATE", (name) => {
    const table = findTable(name);
    expect(table, name).toBeDefined();
    const cfg = getTableConfig(table as never);
    expect(cfg.enableRLS).toBe(true);
    const owner = cfg.policies.find((p) => p.name === `${name}_owner`);
    expect(owner, `${name}_owner`).toBeDefined();
    expect(owner!.for).toBe("all");
    // No TO clause in migrations 0004/0005/0006/0008's CREATE POLICY for any
    // owner policy -- Postgres defaults that to PUBLIC; drizzle's `to` stays
    // unset to reproduce it, rather than the pull's incorrect `["public"]`.
    expect(owner!.to).toBeUndefined();
    expect(owner!.using).toBe(schema.OWNER_PREDICATE);
    expect(owner!.withCheck).toBe(schema.OWNER_PREDICATE);
  });

  it("carries exactly nine owner policies, one per RLS table", () => {
    const names = Object.values(schema)
      .filter(isRlsTable)
      .flatMap((t) => getTableConfig(t as never).policies.map((p) => p.name));
    expect(names.filter((n) => n.endsWith("_owner"))).toHaveLength(9);
  });

  const NON_OWNER = [
    ["turn_run", "turn_run_admin_read", "select", "poseidonAdmin", "true", undefined],
    ["llm_calls", "llm_calls_admin_read", "select", "poseidonAdmin", "true", undefined],
    ["tool_calls", "tool_calls_admin_read", "select", "poseidonAdmin", "true", undefined],
    ["message_feedback", "message_feedback_admin_read", "select", "poseidonAdmin", "true", undefined],
    ["memory_outbox", "memory_outbox_worker", "all", "poseidonWorker", "true", "true"],
  ] as const satisfies ReadonlyArray<
    readonly [string, string, "select" | "all", keyof typeof ROLES, string, string | undefined]
  >;

  it.each(NON_OWNER)(
    "%s carries %s pinned on for/to/using/withCheck",
    (tableName, policyName, forClause, roleKey, usingSql, withCheckSql) => {
      const table = findTable(tableName);
      expect(table, tableName).toBeDefined();
      const cfg = getTableConfig(table as never);
      const policy = cfg.policies.find((p) => p.name === policyName);
      expect(policy, policyName).toBeDefined();
      expect(policy!.for).toBe(forClause);
      expect(policy!.to).toBe(ROLES[roleKey]);
      expect(renderSql(policy!.using)).toBe(usingSql);
      expect(renderSql(policy!.withCheck)).toBe(withCheckSql);
    },
  );

  const INDEXES: Record<string, Array<{ column: string; order: string; nulls: string }>> = {
    ix_tool_calls_turn_run_id_seq: [
      { column: "turn_run_id", order: "asc", nulls: "last" },
      { column: "seq", order: "asc", nulls: "last" },
    ],
    ix_messages_conversation_order: [
      { column: "conversation_id", order: "asc", nulls: "last" },
      { column: "created_at", order: "asc", nulls: "last" },
      { column: "id", order: "asc", nulls: "last" },
    ],
    ix_conversations_user_recency: [
      { column: "user_sub", order: "asc", nulls: "last" },
      { column: "updated_at", order: "desc", nulls: "first" },
      { column: "id", order: "desc", nulls: "first" },
    ],
    ix_turn_run_conversation_id_turn_index: [
      { column: "conversation_id", order: "asc", nulls: "last" },
      { column: "turn_index", order: "asc", nulls: "last" },
    ],
    ix_turn_run_created_at: [{ column: "created_at", order: "asc", nulls: "last" }],
    ix_llm_calls_turn_run_id_seq: [
      { column: "turn_run_id", order: "asc", nulls: "last" },
      { column: "seq", order: "asc", nulls: "last" },
    ],
  };

  it("pins all six indexes by name, column list, order and nulls, with no operator classes", () => {
    const found: Record<string, Array<{ column: string; order: string; nulls: string }>> = {};
    for (const t of Object.values(schema)) {
      if (!isRlsTable(t)) continue;
      for (const idx of getTableConfig(t as never).indexes) {
        const cols = idx.config.columns.map((col) => {
          const c = col as unknown as {
            name?: string;
            indexConfig?: { order?: string; nulls?: string; opClass?: string };
          };
          expect(c.indexConfig?.opClass, `${idx.config.name}.${c.name}`).toBeUndefined();
          return {
            column: c.name ?? "",
            order: c.indexConfig?.order ?? "asc",
            nulls: c.indexConfig?.nulls ?? "last",
          };
        });
        found[idx.config.name as string] = cols;
      }
    }
    expect(Object.keys(found)).toHaveLength(6);
    expect(found).toEqual(INDEXES);
  });
});

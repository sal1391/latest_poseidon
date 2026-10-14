import { execSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const webRoot = join(__dirname, "..", "..");
const drizzleDir = join(webRoot, "drizzle");

describe("web/drizzle is in step with schema.ts (Phase 16 Task 7 tripwire)", () => {
  it("contains the four expected migrations and a journal", () => {
    const files = readdirSync(drizzleDir).filter((f) => f.endsWith(".sql")).sort();
    expect(files).toEqual(["0000_roles.sql", "0001_baseline.sql", "0002_grants_force_rls.sql", "0003_report_run.sql"]);
    expect(existsSync(join(drizzleDir, "meta", "_journal.json"))).toBe(true);
  });

  it("0003 creates report_run as a shared org document and widens the mode check", () => {
    const sql = readFileSync(join(drizzleDir, "0003_report_run.sql"), "utf8");
    expect(sql).toContain('CREATE TABLE "report_run"');
    expect(sql).toContain("GRANT SELECT, INSERT ON report_run TO poseidon_app;");
    expect(sql).not.toMatch(/report_run[\s\S]*ENABLE ROW LEVEL SECURITY/);
    expect(sql).not.toContain("CREATE POLICY");
    expect(sql).toMatch(/ck_conversations_mode[\s\S]*'report'::text/);
  });

  it("the baseline carries all fourteen policies with predicates and no alembic_version", () => {
    const sql = readFileSync(join(drizzleDir, "0001_baseline.sql"), "utf8");
    expect(sql.match(/CREATE POLICY/g)?.length).toBe(14);
    expect(sql.match(/current_setting\('app\.user_sub', true\)/g)?.length).toBe(18); // 9 USING + 9 WITH CHECK
    expect(sql).not.toContain("alembic_version");
    expect(sql.match(/ENABLE ROW LEVEL SECURITY/g)?.length).toBe(9);
  });

  it(
    "drizzle-kit generate against the committed schema produces nothing new",
    { timeout: 90_000 },
    () => {
      const before = readdirSync(drizzleDir).filter((f) => f.endsWith(".sql")).sort();
      const journalBefore = readFileSync(join(drizzleDir, "meta", "_journal.json"), "utf8");
      // drizzle-kit evaluates drizzle.config.ts eagerly, and ruling P16-R9 made
      // that config throw when DRIZZLE_DATABASE_URL is unset -- so `generate`,
      // which never opens a connection, still needs a syntactically valid URL to
      // get past config load. This one is never dialled.
      const out = execSync("npx drizzle-kit generate", {
        cwd: webRoot,
        encoding: "utf8",
        shell: true,
        timeout: 60_000,
        env: { ...process.env, DRIZZLE_DATABASE_URL: "postgresql://unused:unused@127.0.0.1:1/unused" },
      } as never);
      const after = readdirSync(drizzleDir).filter((f) => f.endsWith(".sql")).sort();
      expect(after, out).toEqual(before);
      expect(readFileSync(join(drizzleDir, "meta", "_journal.json"), "utf8")).toBe(journalBefore);
    },
  );
});

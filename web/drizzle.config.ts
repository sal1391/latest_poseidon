import { defineConfig } from "drizzle-kit";

// Ruling P16-R9: no fallback URL. Since Phase 16 Task 8 `drizzle-kit migrate`
// is a live command that compose runs at boot, and on this developer's machine
// host port 5432 belongs to an unrelated project's Postgres -- a default would
// silently point a migration at the wrong database. Compose's `migrate`
// service sets DRIZZLE_DATABASE_URL; set it yourself for any local
// drizzle-kit command.
function migrationUrl(): string {
  const url = process.env.DRIZZLE_DATABASE_URL;
  if (url === undefined || url.trim() === "") {
    throw new Error(
      "DRIZZLE_DATABASE_URL is unset: refusing to guess which database to migrate",
    );
  }
  return url;
}

export default defineConfig({
  dialect: "postgresql",
  schema: "./src/db/schema.ts",
  out: "./drizzle",
  dbCredentials: {
    url: migrationUrl(),
  },
  // "app" does not exist in this database -- the real schemas are "public"
  // (app state, introspected here) and "synthetic" (migration 0002's
  // certified mock analytics data), and "synthetic" is deliberately excluded:
  // it is not app state, drizzle-kit has no business introspecting it, and
  // the query builder reaches it through the certified ontology, never
  // through this schema.
  schemaFilter: ["public"],
  tablesFilter: ["*"],
});

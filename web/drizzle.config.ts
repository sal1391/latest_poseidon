import { defineConfig } from "drizzle-kit";

export default defineConfig({
  dialect: "postgresql",
  schema: "./src/db/schema.ts",
  dbCredentials: {
    url:
      process.env.DRIZZLE_DATABASE_URL ??
      "postgresql://poseidon:poseidon@localhost:5432/poseidon",
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

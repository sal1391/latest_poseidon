import { defineConfig } from "drizzle-kit";

export default defineConfig({
  dialect: "postgresql",
  schema: "./src/db/schema.ts",
  dbCredentials: {
    url:
      process.env.DRIZZLE_DATABASE_URL ??
      "postgresql://poseidon:poseidon@localhost:5432/poseidon",
  },
  schemaFilter: ["app", "public"],
  tablesFilter: ["*"],
});

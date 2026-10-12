import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node", // db and lib tests; component tests opt into jsdom per file
  },
});

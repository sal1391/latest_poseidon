import postgres from "postgres";
import { appRole, assertBootPrivileges } from "./client";

/** One short-lived connection, closed before the app serves anything. */
export async function bootCheck(): Promise<void> {
  const url =
    process.env.DRIZZLE_DATABASE_URL ?? "postgresql://poseidon:poseidon@localhost:5432/poseidon";
  const client = postgres(url, { max: 1, connect_timeout: 5 });
  try {
    await assertBootPrivileges(client, appRole());
  } finally {
    await client.end();
  }
}

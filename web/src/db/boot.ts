import postgres from "postgres";
import { appRole, assertBootPrivileges, databaseUrl } from "./client";

/** One short-lived connection, closed before the app serves anything.
 * Throws before connecting when DRIZZLE_DATABASE_URL is unset (P16-R14). */
export async function bootCheck(): Promise<void> {
  const client = postgres(databaseUrl(), { max: 1, connect_timeout: 5 });
  try {
    await assertBootPrivileges(client, appRole());
  } finally {
    await client.end();
  }
}

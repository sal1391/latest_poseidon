"use server";

import { revalidatePath } from "next/cache";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { createConversation } from "./conversations";
import { requireSales } from "./request-identity";

/**
 * The New chat action, shared by the sidebar and the empty state. The root
 * layout renders the conversation list, and a layout is not re-fetched on a
 * soft navigation, so the new row would not appear without the layout-scoped
 * revalidation before the redirect.
 */
export async function newConversation(): Promise<void> {
  const { sub } = requireSales(await headers());
  const id = await createConversation(sub);
  revalidatePath("/", "layout");
  redirect(`/c/${id}`);
}

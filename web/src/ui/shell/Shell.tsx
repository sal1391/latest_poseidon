"use client";

import { useState, type ReactNode } from "react";

/**
 * The app shell (spec 3.2, 3.4). On desktop a 260px sidebar and a fluid
 * main column fill the viewport; the thread scrolls inside main. Below the
 * md breakpoint the sidebar hides behind a Chats toggle (ruling P1-R3: the
 * toggle lives here, not in the page header, because the open state must sit
 * above both) and opens as a fixed overlay with a scrim. Any click inside
 * the open sidebar closes it, so choosing a row never leaves the scrim up.
 */
export function Shell({ sidebar, children }: { sidebar: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="grid h-full grid-cols-1 grid-rows-[auto_1fr] md:grid-cols-[260px_1fr] md:grid-rows-1">
      <div className="flex items-center gap-3 border-b border-border px-4 py-2 md:hidden">
        <button
          type="button"
          aria-expanded={open}
          aria-controls="sidebar"
          onClick={() => setOpen((value) => !value)}
          className="rounded-chip border border-border bg-surface px-3 py-1 text-[13px]"
        >
          Chats
        </button>
        <span className="text-[13px] font-semibold">Poseidon</span>
      </div>
      <div
        id="sidebar"
        onClick={() => setOpen(false)}
        className={`${open ? "fixed inset-y-0 left-0 z-20 w-[260px] shadow-2" : "hidden"} md:static md:block md:w-auto md:shadow-none`}
      >
        {sidebar}
      </div>
      {open ? (
        <button type="button" aria-label="Close conversations" onClick={() => setOpen(false)} className="fixed inset-0 z-10 bg-ink/20 md:hidden" />
      ) : null}
      <main className="flex min-h-0 min-w-0 flex-col">{children}</main>
    </div>
  );
}

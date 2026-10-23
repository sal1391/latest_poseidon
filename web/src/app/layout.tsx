import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Poseidon",
  description: "Chat-first sales intelligence for marine fuel sales",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="h-dvh bg-surface font-sans text-ink">{children}</body>
    </html>
  );
}

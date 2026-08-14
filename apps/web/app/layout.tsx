import type { Metadata } from "next";
import { Chivo, IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import "./globals.css";
import { SessionProvider } from "@/lib/session";

/* Chivo carries headlines: an industrial grotesque with tight apertures that
   reads as instrumentation rather than marketing. Plex Sans handles body copy,
   Plex Mono carries every label, metric and stage name. */
const display = Chivo({ subsets: ["latin"], weight: ["500", "700", "900"], variable: "--font-display" });
const body = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-body" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "InsightOS — From Business Questions to Autonomous Decisions",
  description:
    "InsightOS investigates your data, discovers hidden patterns, predicts what happens next, and recommends what to do.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} ${mono.variable}`}>
      <body>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-elevated focus:px-4 focus:py-2"
        >
          Skip to content
        </a>
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}

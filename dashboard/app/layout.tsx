import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import { FeedProvider } from "@/components/Feed";
import { Shell } from "@/components/Shell";
import "./globals.css";

const sans = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex-sans" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-plex-mono" });

export const metadata: Metadata = {
  title: "AccountableAI · Oakline Homes",
  description: "Confirm the middleman paid your vendors, chase them when they didn't, and warn before the next funding.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>
        <FeedProvider>
          <Shell>{children}</Shell>
        </FeedProvider>
      </body>
    </html>
  );
}

import type { Metadata } from "next";
import { Fira_Sans } from "next/font/google";
import type { ReactNode } from "react";

import { Providers } from "./providers";
import "./globals.css";

const firaSans = Fira_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-fira-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "WorkPulse",
  description: "Real-time workforce management and people analytics",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${firaSans.variable} font-sans antialiased`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}

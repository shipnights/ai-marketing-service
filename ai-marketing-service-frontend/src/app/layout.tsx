import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AI Marketing Service — From zero strategy to published content",
  description:
    "The only platform that takes you from zero strategy to published content in one workflow. Audience research, AI ideas, AI video generation, and auto-publishing all in one place.",
  keywords: [
    "AI marketing",
    "social media automation",
    "AI content generation",
    "Instagram scheduling",
    "TikTok automation",
    "AI video generation",
    "marketing plan generator",
  ].join(", "),
  openGraph: {
    title: "AI Marketing Service — From zero strategy to published content",
    description:
      "Audience research, AI ideas, AI video, and auto-publishing — all in one workflow.",
    type: "website",
    siteName: "AI Marketing Service",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        {children}
      </body>
    </html>
  );
}

import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Meridian Capital",
  description: "Trading desk — GANESH KAVACH 50K",
  applicationName: "Meridian Capital",
  robots: { index: false, follow: false, nocache: true },
  // Added to the home screen, the desk opens full-screen under its own name
  // instead of as a Safari tab. The status bar goes translucent so the header
  // runs to the top edge; AppShell pads for the notch itself.
  appleWebApp: {
    capable: true,
    title: "Meridian",
    statusBarStyle: "black-translucent",
  },
  // iOS otherwise turns strike prices and order ids into tappable phone links.
  formatDetection: { telephone: false, email: false, address: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Draw under the notch and home indicator; the layout keeps content out of
  // them with env(safe-area-inset-*). Without this those insets read as 0.
  viewportFit: "cover",
  themeColor: "#0a0a0a",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

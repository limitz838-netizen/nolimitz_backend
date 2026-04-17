import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NolimitzBots",
  description:
    "Take full advantage of Artificial Intelligence directly on your mobile phone. Simple, fast, and powerful automation.",
  icons: {
    icon: "/favicon.png",
  },
  openGraph: {
    title: "NolimitzBots",
    description:
      "Take full advantage of Artificial Intelligence directly on your mobile phone. Simple, fast, and powerful automation.",
    url: "https://nolimitzbots.co.ke",
    siteName: "NolimitzBots",
    images: [
      {
        url: "/og-image.jpg",
        width: 1200,
        height: 630,
        alt: "NolimitzBots",
      },
    ],
    locale: "en_US",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "NolimitzBots",
    description:
      "Take full advantage of Artificial Intelligence directly on your mobile phone. Simple, fast, and powerful automation.",
    images: ["/og-image.jpg"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
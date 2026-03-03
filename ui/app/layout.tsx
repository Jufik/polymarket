import type { Metadata } from "next";
import "./globals.css";
import { NavBar } from "./NavBar";

export const metadata: Metadata = {
  title: "Polymarket Trading",
  description: "Trading dashboard for Polymarket",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <NavBar />
        {children}
      </body>
    </html>
  );
}

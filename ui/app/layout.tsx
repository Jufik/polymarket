import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Polymarket Trading",
  description: "Trading dashboard for Polymarket",
};

function NavBar() {
  return (
    <nav className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center gap-6">
      <span className="text-sm font-semibold text-gray-200 tracking-wide">
        PM Trading
      </span>
      <Link
        href="/"
        className="text-sm text-gray-400 hover:text-gray-200 transition-colors"
      >
        Dashboard
      </Link>
      <Link
        href="/markets"
        className="text-sm text-gray-400 hover:text-gray-200 transition-colors"
      >
        Markets
      </Link>
      <Link
        href="/pnl"
        className="text-sm text-gray-400 hover:text-gray-200 transition-colors"
      >
        PnL
      </Link>
      <Link
        href="/insiders"
        className="text-sm text-gray-400 hover:text-gray-200 transition-colors"
      >
        Insiders
      </Link>
    </nav>
  );
}

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

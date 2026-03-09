"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [{ href: "/", label: "Dashboard" }] as const;

export function NavBar() {
  const pathname = usePathname();

  return (
    <nav className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center gap-6">
      <span className="text-sm font-semibold text-gray-200 tracking-wide">
        PM Trading
      </span>
      {LINKS.map(({ href, label }) => {
        const active =
          href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={`text-sm transition-colors ${
              active
                ? "text-blue-400 font-medium"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

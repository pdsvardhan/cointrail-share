"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ThemeProvider } from "next-themes";
import { Receipt, CreditCard, Repeat } from "lucide-react";

const navItems = [
  { name: "Cards", href: "/cards", icon: CreditCard },
  { name: "Expenses", href: "/expenses", icon: Receipt },
  { name: "Recurring", href: "/expenses/recurring", icon: Repeat },
];

function LayoutInner({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  const isActive = (href: string) => {
    if (href === "/cards") return pathname === "/" || pathname.startsWith("/cards");
    if (href === "/expenses/recurring") return pathname.startsWith("/expenses/recurring");
    if (href === "/expenses")
      return pathname.startsWith("/expenses") && !pathname.startsWith("/expenses/recurring");
    return pathname.startsWith(href);
  };

  return (
    <>
      {/* Header with inline page switcher */}
      <header className="fixed top-0 right-0 left-0 z-40 flex h-14 items-center justify-between gap-3 border-b border-[var(--border)] bg-[var(--background)]/80 px-4 backdrop-blur-xl">
        <Link href="/cards" className="flex shrink-0 items-center">
          <span className="bg-gradient-to-r from-blue-400 via-violet-400 to-purple-400 bg-clip-text text-xl font-extrabold text-transparent">
            Cointrail
          </span>
        </Link>

        <nav className="flex items-center gap-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = isActive(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-label={item.name}
                aria-current={active ? "page" : undefined}
                className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition-all duration-200 ${
                  active
                    ? "bg-blue-500/10 text-blue-400"
                    : "text-[var(--foreground-secondary)] hover:bg-[var(--card-hover)] hover:text-[var(--foreground)]"
                }`}
              >
                <Icon className="h-[18px] w-[18px] shrink-0" />
                <span className="hidden sm:inline">{item.name}</span>
              </Link>
            );
          })}
        </nav>
      </header>

      {/* Main content */}
      <main className="pt-14">
        <div className="p-4 md:p-6">{children}</div>
      </main>
    </>
  );
}

export function ClientLayout({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider attribute="class" forcedTheme="dark" enableSystem={false}>
      <LayoutInner>{children}</LayoutInner>
    </ThemeProvider>
  );
}

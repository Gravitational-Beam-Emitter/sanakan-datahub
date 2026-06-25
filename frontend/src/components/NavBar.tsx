"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/", label: "复盘" },
  { href: "/backtest", label: "回测" },
  { href: "/sectors", label: "题材轮动" },
  { href: "/cross", label: "宏观交叉" },
  { href: "/us-corp-actions", label: "US公司行动" },
  { href: "/us-listings", label: "US上市" },
  { href: "/hk-funds", label: "HK基金" },
  { href: "/announcements", label: "公告" },
  { href: "/tw-stock", label: "TW台股" },
  { href: "/kr-stock", label: "KR韩股" },
  { href: "/industry-chain", label: "产业链" },
];

export default function NavBar() {
  const pathname = usePathname();

  return (
    <nav className="glass rounded-xl px-2 py-1.5 flex gap-0.5 mx-auto w-fit">
      {TABS.map((t) => {
        const active = t.href === "/" ? pathname === "/" : pathname.startsWith(t.href);
        return (
          <Link
            key={t.href}
            href={t.href}
            className={`text-sm px-4 py-2 rounded-lg transition-all ${
              active
                ? "bg-primary-a15 text-primary font-medium"
                : "text-muted hover:text-ink hover:bg-surface-hover"
            }`}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}

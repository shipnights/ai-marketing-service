"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import styles from "./Sidebar.module.css";

const NAV = [
  { href: "/dashboard", label: "Projects", icon: "◫" },
  { href: "/dashboard/ideas", label: "Ideas", icon: "✦" },
  { href: "/dashboard/posts", label: "Posts", icon: "⊞" },
  { href: "/dashboard/schedule", label: "Schedule", icon: "◷" },
  { href: "/dashboard/analytics", label: "Analytics", icon: "◈" },
  { href: "/dashboard/ads", label: "Ad Manager", icon: "◎" },
  { href: "/dashboard/integrations", label: "Integrations", icon: "⊹" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className={styles.sidebar}>
      <div className={styles.brand}>
        <span className={styles.brandIcon}>✦</span>
        <span className={styles.brandName}>AMS</span>
      </div>

      <nav className={styles.nav}>
        {NAV.map(({ href, label, icon }) => {
          const active = pathname === href || (href !== "/dashboard" && pathname.startsWith(href));
          return (
            <Link
              key={href}
              href={href}
              className={`${styles.navItem} ${active ? styles.active : ""}`}
            >
              <span className={styles.navIcon}>{icon}</span>
              <span className={styles.navLabel}>{label}</span>
            </Link>
          );
        })}
      </nav>

      <div className={styles.bottom}>
        <Link
          href="/dashboard/billing"
          className={`${styles.navItem} ${
            pathname.startsWith("/dashboard/billing") ? styles.active : ""
          }`}
        >
          <span className={styles.navIcon}>▤</span>
          <span className={styles.navLabel}>Billing</span>
        </Link>
        <Link href="/dashboard/settings" className={styles.navItem}>
          <span className={styles.navIcon}>⚙</span>
          <span className={styles.navLabel}>Settings</span>
        </Link>
      </div>
    </aside>
  );
}

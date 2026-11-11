"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import CreditBalance from "@/components/CreditBalance";
import { STORAGE_KEYS } from "@/lib/storage";
import styles from "./layout.module.css";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();

  useEffect(() => {
    const token = localStorage.getItem(STORAGE_KEYS.accessToken);
    if (!token) {
      router.replace("/auth?mode=login");
    }
  }, [router]);

  return (
    <div className={styles.shell}>
      <Sidebar />
      <div className={styles.main}>
        <header className={styles.topbar}>
          <div className={styles.topbarRight}>
            <CreditBalance />
          </div>
        </header>
        <div className={styles.content}>{children}</div>
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { STORAGE_KEYS } from "@/lib/storage";
import styles from "./CreditBalance.module.css";

interface Team {
  ai_credits?: number;
  [key: string]: unknown;
}

export default function CreditBalance() {
  const [credits, setCredits] = useState<number | null>(null);

  useEffect(() => {
    const raw = localStorage.getItem(STORAGE_KEYS.team);
    if (raw) {
      try {
        const team: Team = JSON.parse(raw);
        if (typeof team.ai_credits === "number") {
          setCredits(team.ai_credits);
        }
      } catch {
        // malformed storage — ignore
      }
    }
  }, []);

  if (credits === null) return null;

  return (
    <div className={styles.container}>
      <span className={styles.icon}>✦</span>
      <span className={styles.label}>{credits.toLocaleString()} credits</span>
    </div>
  );
}

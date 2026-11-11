import styles from "./MetricCard.module.css";

interface Props {
  label: string;
  value: string;
  sub?: string;
  accent?: boolean;
}

export default function MetricCard({ label, value, sub, accent }: Props) {
  return (
    <div className={`${styles.card} ${accent ? styles.accent : ""}`}>
      <span className={styles.label}>{label}</span>
      <span className={styles.value}>{value}</span>
      {sub && <span className={styles.sub}>{sub}</span>}
    </div>
  );
}

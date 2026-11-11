import type { ProjectStatus } from "@/lib/projects-api";
import styles from "./StatusBadge.module.css";

interface Props {
  status: ProjectStatus | string;
}

const LABEL: Record<string, string> = {
  draft: "Draft",
  active: "Active",
  archived: "Archived",
};

export default function StatusBadge({ status }: Props) {
  const label = LABEL[status] ?? status;
  return (
    <span className={`${styles.badge} ${styles[status] ?? ""}`}>{label}</span>
  );
}

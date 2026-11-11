import styles from "./PlatformBadge.module.css";

interface Props {
  platform: string;
}

const ICONS: Record<string, string> = {
  instagram: "IG",
  facebook: "FB",
  tiktok: "TT",
  linkedin: "LI",
  twitter: "TW",
};

export default function PlatformBadge({ platform }: Props) {
  const label = ICONS[platform.toLowerCase()] ?? platform.slice(0, 2).toUpperCase();
  return (
    <span className={`${styles.badge} ${styles[platform.toLowerCase()] ?? ""}`}>
      {label}
    </span>
  );
}

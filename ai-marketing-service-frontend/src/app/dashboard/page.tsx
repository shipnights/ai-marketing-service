"use client";

import { useEffect, useState } from "react";
import { listProjects, type Project, type ProjectStatus } from "@/lib/projects-api";
import StatusBadge from "@/components/StatusBadge";
import PlatformBadge from "@/components/PlatformBadge";
import styles from "./page.module.css";

const TABS: { label: string; value: ProjectStatus | undefined }[] = [
  { label: "All", value: undefined },
  { label: "Active", value: "active" },
  { label: "Draft", value: "draft" },
  { label: "Archived", value: "archived" },
];

export default function DashboardPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<ProjectStatus | undefined>(undefined);

  useEffect(() => {
    setLoading(true);
    listProjects(tab).then(({ data, error }) => {
      if (error) {
        setError(error);
      } else {
        setProjects(data ?? []);
        setError(null);
      }
      setLoading(false);
    });
  }, [tab]);

  return (
    <div>
      <div className={styles.header}>
        <h1 className={styles.title}>Projects</h1>
        <a href="/dashboard/projects/new" className={styles.newBtn}>
          + New Project
        </a>
      </div>

      <div className={styles.tabs}>
        {TABS.map(({ label, value }) => (
          <button
            key={label}
            className={`${styles.tab} ${tab === value ? styles.activeTab : ""}`}
            onClick={() => setTab(value)}
          >
            {label}
          </button>
        ))}
      </div>

      {loading && <p className={styles.empty}>Loading…</p>}
      {!loading && error && <p className={styles.error}>{error}</p>}
      {!loading && !error && projects.length === 0 && (
        <div className={styles.emptyState}>
          <p className={styles.emptyTitle}>No projects yet</p>
          <p className={styles.emptyText}>
            Create your first project to start generating AI marketing content.
          </p>
          <a href="/dashboard/projects/new" className={styles.newBtn}>
            + New Project
          </a>
        </div>
      )}

      {!loading && !error && projects.length > 0 && (
        <div className={styles.grid}>
          {projects.map((project) => (
            <a
              key={project.project_id}
              href={`/dashboard/projects/${project.project_id}`}
              className={styles.card}
            >
              <div className={styles.cardHeader}>
                <span className={styles.cardName}>{project.name}</span>
                <StatusBadge status={project.status} />
              </div>
              {project.description && (
                <p className={styles.cardDesc}>{project.description}</p>
              )}
              {project.platforms.length > 0 && (
                <div className={styles.platforms}>
                  {project.platforms.map((p) => (
                    <PlatformBadge key={p} platform={p} />
                  ))}
                </div>
              )}
              <p className={styles.cardDate}>
                Updated {new Date(project.updated_at).toLocaleDateString()}
              </p>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

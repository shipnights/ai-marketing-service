"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  login,
  registerStep1,
  registerStep2,
  registerStep3,
  registerStep4,
  storeAuthSession,
} from "@/lib/auth-api";
import { STORAGE_KEYS } from "@/lib/storage";
import styles from "./auth.module.css";

// ============================================================================
// Inner component (uses useSearchParams — must be wrapped in Suspense)
// ============================================================================

function AuthForm() {
  const router = useRouter();
  const params = useSearchParams();
  const initialMode = params.get("mode") === "register" ? "register" : "login";

  const [mode, setMode] = useState<"login" | "register">(initialMode);
  const [step, setStep] = useState(1); // register wizard step (1–4)

  // Shared state
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [teamName, setTeamName] = useState("");
  const [tempToken, setTempToken] = useState("");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Redirect if already logged in
  useEffect(() => {
    const token = localStorage.getItem(STORAGE_KEYS.accessToken);
    if (token) router.replace("/dashboard");
  }, [router]);

  function switchMode(next: "login" | "register") {
    setMode(next);
    setStep(1);
    setError(null);
  }

  // ── Login ──────────────────────────────────────────────────────────────────
  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const { data, error: err } = await login({ email, password });
    setLoading(false);
    if (err) {
      setError(err);
      return;
    }
    storeAuthSession(data);
    router.push("/dashboard");
  }

  // ── Register step 1 ───────────────────────────────────────────────────────
  async function handleStep1(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const { data, error: err } = await registerStep1({ email, password });
    setLoading(false);
    if (err) {
      setError(err);
      return;
    }
    setTempToken(data.tempToken);
    setStep(2);
  }

  // ── Register step 2 ───────────────────────────────────────────────────────
  async function handleStep2(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const { error: err } = await registerStep2({ temp_token: tempToken, full_name: fullName });
    setLoading(false);
    if (err) {
      setError(err);
      return;
    }
    setStep(3);
  }

  // ── Register step 3 ───────────────────────────────────────────────────────
  async function handleStep3(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const { data, error: err } = await registerStep3({ temp_token: tempToken, team_name: teamName });
    setLoading(false);
    if (err) {
      setError(err);
      return;
    }
    if (data.tempToken) setTempToken(data.tempToken);
    setStep(4);
  }

  // ── Register step 4 ───────────────────────────────────────────────────────
  async function handleStep4(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const { data, error: err } = await registerStep4(tempToken);
    setLoading(false);
    if (err) {
      setError(err);
      return;
    }
    storeAuthSession(data);
    router.push("/dashboard");
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className={styles.page}>
      <div className={styles.card}>
        <div className={styles.brand}>
          <span className={styles.brandIcon}>✦</span>
          <span className={styles.brandName}>AI Marketing Service</span>
        </div>

        {/* Tab switcher */}
        {mode !== "register" || step === 1 ? (
          <div className={styles.tabs}>
            <button
              className={`${styles.tab} ${mode === "login" ? styles.activeTab : ""}`}
              onClick={() => switchMode("login")}
            >
              Sign in
            </button>
            <button
              className={`${styles.tab} ${mode === "register" ? styles.activeTab : ""}`}
              onClick={() => switchMode("register")}
            >
              Create account
            </button>
          </div>
        ) : (
          <div className={styles.stepIndicator}>
            {[1, 2, 3, 4].map((s) => (
              <div
                key={s}
                className={`${styles.stepDot} ${s <= step ? styles.stepDotActive : ""}`}
              />
            ))}
          </div>
        )}

        {error && <div className={styles.errorBanner}>{error}</div>}

        {/* Login */}
        {mode === "login" && (
          <form onSubmit={handleLogin} className={styles.form}>
            <label className={styles.label}>
              Email
              <input
                type="email"
                className={styles.input}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
              />
            </label>
            <label className={styles.label}>
              Password
              <input
                type="password"
                className={styles.input}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
              />
            </label>
            <button type="submit" className={styles.btn} disabled={loading}>
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>
        )}

        {/* Register step 1 — credentials */}
        {mode === "register" && step === 1 && (
          <form onSubmit={handleStep1} className={styles.form}>
            <p className={styles.stepTitle}>Create your account</p>
            <label className={styles.label}>
              Email
              <input
                type="email"
                className={styles.input}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
              />
            </label>
            <label className={styles.label}>
              Password
              <input
                type="password"
                className={styles.input}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                autoComplete="new-password"
              />
            </label>
            <button type="submit" className={styles.btn} disabled={loading}>
              {loading ? "Creating account…" : "Continue"}
            </button>
          </form>
        )}

        {/* Register step 2 — profile */}
        {mode === "register" && step === 2 && (
          <form onSubmit={handleStep2} className={styles.form}>
            <p className={styles.stepTitle}>Tell us about yourself</p>
            <label className={styles.label}>
              Full name
              <input
                type="text"
                className={styles.input}
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                required
                autoComplete="name"
              />
            </label>
            <button type="submit" className={styles.btn} disabled={loading}>
              {loading ? "Saving…" : "Continue"}
            </button>
          </form>
        )}

        {/* Register step 3 — team */}
        {mode === "register" && step === 3 && (
          <form onSubmit={handleStep3} className={styles.form}>
            <p className={styles.stepTitle}>Name your team or brand</p>
            <label className={styles.label}>
              Team name
              <input
                type="text"
                className={styles.input}
                value={teamName}
                onChange={(e) => setTeamName(e.target.value)}
                required
              />
            </label>
            <button type="submit" className={styles.btn} disabled={loading}>
              {loading ? "Creating team…" : "Continue"}
            </button>
          </form>
        )}

        {/* Register step 4 — complete */}
        {mode === "register" && step === 4 && (
          <form onSubmit={handleStep4} className={styles.form}>
            <p className={styles.stepTitle}>
              You&#39;re all set, {fullName.split(" ")[0]}!
            </p>
            <p className={styles.stepSub}>
              Your team <strong>{teamName}</strong> is ready. Click below to open
              your dashboard and start your first project.
            </p>
            <button type="submit" className={styles.btn} disabled={loading}>
              {loading ? "Setting up…" : "Go to dashboard"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// Page export (wraps inner form in Suspense for useSearchParams)
// ============================================================================

export default function AuthPage() {
  return (
    <Suspense>
      <AuthForm />
    </Suspense>
  );
}

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Mail, Lock, Eye, EyeOff, Shield } from "lucide-react";
import { getApiBaseUrl, saveAdminToken } from "@/lib/admin-auth";

export default function AdminLoginPage() {
  const router = useRouter();

  const [showPassword, setShowPassword] = useState(false);
  const [focusedField, setFocusedField] = useState<"email" | "password" | null>(null);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  const getInputWrapClass = (field: "email" | "password") =>
    `group flex h-14 items-center rounded-2xl border px-5 transition-all duration-300 ${
      focusedField === field
        ? "border-cyan-300/70 bg-white/10 shadow-[0_0_0_1px_rgba(103,232,249,0.20),0_0_26px_rgba(34,211,238,0.18),inset_0_1px_0_rgba(255,255,255,0.08)]"
        : "border-white/10 bg-white/5 hover:border-white/20 hover:bg-white/[0.07]"
    }`;

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setErrorMessage("");
    setLoading(true);

    try {
      const response = await fetch(`${getApiBaseUrl()}/admin/login`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email: email.trim(),
          password,
        }),
      });

      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(
          data?.detail ||
            data?.message ||
            "Invalid email or password. Please try again."
        );
      }

      if (!data?.access_token) {
        throw new Error("Login succeeded but no access token was received.");
      }

      if (data.is_approved === false) {
        throw new Error("Your admin account is pending approval.");
      }

      saveAdminToken(data.access_token);
      router.push("/admin/dashboard");
    } catch (error: any) {
      setErrorMessage(
        error?.message || "Something went wrong. Please try again later."
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen overflow-hidden bg-[#020817] text-white">
      <div className="relative min-h-screen bg-[radial-gradient(circle_at_top,rgba(59,130,246,0.22),transparent_35%),radial-gradient(circle_at_bottom,rgba(34,211,238,0.12),transparent_30%),linear-gradient(180deg,#020817_0%,#07152b_55%,#0a1f44_100%)] px-4 py-6 sm:px-6">
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="absolute left-[-120px] top-20 h-72 w-72 rounded-full bg-cyan-400/10 blur-3xl" />
          <div className="absolute right-[-100px] top-40 h-80 w-80 rounded-full bg-blue-500/10 blur-3xl" />
          <div className="absolute bottom-0 left-1/2 h-72 w-72 -translate-x-1/2 rounded-full bg-violet-500/10 blur-3xl" />
        </div>

        <div className="relative mx-auto flex min-h-screen w-full max-w-md items-center justify-center">
          <div className="w-full rounded-[30px] border border-white/10 bg-white/5 p-5 shadow-2xl backdrop-blur-2xl sm:p-7">
            <div className="mb-8 flex flex-col items-center text-center">
              <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-[22px] border border-cyan-400/25 bg-white/5 shadow-[0_0_30px_rgba(34,211,238,0.10)]">
                <Shield className="h-9 w-9 text-cyan-300" />
              </div>

              <h1 className="bg-gradient-to-r from-cyan-300 via-sky-400 to-blue-500 bg-clip-text text-4xl font-black tracking-tighter text-transparent sm:text-5xl">
                NolimitzBots
              </h1>
              <p className="mt-1 text-base font-medium text-white/60">Admin Portal</p>
            </div>

            <div className="rounded-[28px] border border-white/10 bg-white/5 p-6 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] backdrop-blur-xl sm:p-8">
              <h2 className="text-3xl font-bold tracking-tight text-white">Welcome back</h2>
              <p className="mt-3 text-[15px] leading-relaxed text-white/70">
                Sign in to manage licenses, Expert Advisors, analytics, users, and platform tools.
              </p>

              <form onSubmit={handleSubmit} className="mt-8 space-y-6">
                <div>
                  <label className="mb-2 block text-sm font-medium text-white/90">
                    Email Address
                  </label>
                  <div className={getInputWrapClass("email")}>
                    <Mail
                      className={`mr-3 h-5 w-5 transition-colors ${
                        focusedField === "email" ? "text-cyan-300" : "text-cyan-300/75"
                      }`}
                    />
                    <input
                      type="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="admin@nolimitzbots.com"
                      className="flex-1 bg-transparent text-base text-white placeholder:text-white/40 focus:outline-none"
                      onFocus={() => setFocusedField("email")}
                      onBlur={() => setFocusedField(null)}
                      autoComplete="email"
                    />
                  </div>
                </div>

                <div>
                  <label className="mb-2 block text-sm font-medium text-white/90">
                    Password
                  </label>
                  <div className={getInputWrapClass("password")}>
                    <Lock
                      className={`mr-3 h-5 w-5 transition-colors ${
                        focusedField === "password" ? "text-cyan-300" : "text-cyan-300/75"
                      }`}
                    />
                    <input
                      type={showPassword ? "text" : "password"}
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="Enter your password"
                      className="flex-1 bg-transparent text-base text-white placeholder:text-white/40 focus:outline-none"
                      onFocus={() => setFocusedField("password")}
                      onBlur={() => setFocusedField(null)}
                      autoComplete="current-password"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((prev) => !prev)}
                      className="ml-2 text-white/60 transition-colors hover:text-cyan-300"
                      aria-label={showPassword ? "Hide password" : "Show password"}
                    >
                      {showPassword ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
                    </button>
                  </div>
                </div>

                <div className="flex justify-end">
                  <Link
                    href="/admin/forgot-password"
                    className="text-sm font-medium text-cyan-400 transition-colors hover:text-cyan-300 hover:underline"
                  >
                    Forgot password?
                  </Link>
                </div>

                {errorMessage && (
                  <div className="rounded-2xl border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
                    {errorMessage}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={loading}
                  className="mt-2 flex h-14 w-full items-center justify-center gap-3 rounded-2xl bg-gradient-to-r from-cyan-400 via-blue-500 to-cyan-300 text-lg font-bold text-slate-950 shadow-[0_0_30px_rgba(56,189,248,0.28)] transition-all duration-300 hover:scale-[1.015] hover:shadow-[0_0_45px_rgba(56,189,248,0.45)] active:scale-[0.985] disabled:cursor-not-allowed disabled:opacity-75"
                >
                  {loading ? (
                    "Signing in..."
                  ) : (
                    <>
                      Sign In
                      <span className="text-xl">→</span>
                    </>
                  )}
                </button>
              </form>

              <div className="mt-8 text-center">
                <p className="text-sm text-white/60">
                  Don&apos;t have an account?{" "}
                  <Link
                    href="/admin/signup"
                    className="font-semibold text-cyan-400 transition-colors hover:text-cyan-300"
                  >
                    Sign up
                  </Link>
                </p>

                <div className="mt-6 flex items-center justify-center gap-2 text-xs text-white/30">
                  <div className="h-px w-6 bg-white/10" />
                  <span>Secure Admin Access</span>
                  <div className="h-px w-6 bg-white/10" />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
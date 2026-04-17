"use client";

import Link from "next/link";
import { useState } from "react";
import { Mail, ShieldCheck, ArrowLeft } from "lucide-react";

export default function ForgotPasswordPage() {
  const [focusedField, setFocusedField] = useState<string | null>(null);
  const [isSubmitted, setIsSubmitted] = useState(false);

  const inputWrap = (name: string) =>
    `flex h-16 items-center rounded-3xl border px-6 transition-all duration-300 ${
      focusedField === name
        ? "border-cyan-400/70 bg-white/10 shadow-[0_0_0_1px_rgba(103,232,249,0.2),0_0_35px_rgba(34,211,238,0.15)]"
        : "border-white/10 bg-white/6 hover:border-white/20"
    }`;

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    // TODO: Add your password reset logic here
    setIsSubmitted(true);
    console.log("Password reset link requested");
  };

  return (
    <main className="min-h-screen bg-[#020817] text-white overflow-hidden">
      <div className="relative min-h-screen bg-[radial-gradient(circle_at_top,rgba(34,211,238,0.14),transparent_30%),linear-gradient(180deg,#020817_0%,#08142e_50%,#0a1f44_100%)] px-4 py-8 sm:px-6">
        
        <div className="mx-auto flex min-h-screen max-w-[820px] items-center justify-center">
          <div className="w-full rounded-3xl border border-white/10 bg-white/5 p-8 shadow-2xl backdrop-blur-2xl sm:p-10">
            
            {/* Header */}
            <div className="mb-10 flex flex-col items-center text-center">
              <div className="mb-5 flex h-20 w-20 items-center justify-center rounded-3xl border border-cyan-400/30 bg-white/5">
                <ShieldCheck className="h-10 w-10 text-cyan-300" />
              </div>

              <h1 className="bg-gradient-to-r from-cyan-300 via-sky-400 to-blue-500 bg-clip-text text-5xl font-black tracking-tighter text-transparent sm:text-6xl">
                NolimitzBots
              </h1>
              <p className="mt-2 text-xl font-medium text-white/60">Admin Portal</p>
            </div>

            {/* Form Container */}
            <div className="mx-auto max-w-[640px] rounded-3xl border border-white/10 bg-white/5 p-8 sm:p-10">
              
              {!isSubmitted ? (
                <>
                  <h2 className="text-4xl font-bold tracking-tight text-white sm:text-5xl">
                    Forgot password
                  </h2>

                  <p className="mt-4 text-lg leading-relaxed text-white/70">
                    Enter your admin email address and we&apos;ll send you a link to reset your password.
                  </p>

                  <form onSubmit={handleSubmit} className="mt-10 space-y-7">
                    <div className="space-y-2.5">
                      <label className="text-sm font-medium text-white/90">Email Address</label>
                      <div className={inputWrap("email")}>
                        <Mail className="mr-4 h-5 w-5 text-cyan-300/80" />
                        <input
                          type="email"
                          required
                          placeholder="admin@nolimitzbots.com"
                          className="h-full flex-1 bg-transparent text-lg text-white outline-none placeholder:text-white/40"
                          onFocus={() => setFocusedField("email")}
                          onBlur={() => setFocusedField(null)}
                        />
                      </div>
                    </div>

                    <button
                      type="submit"
                      className="mt-4 flex h-16 w-full items-center justify-center rounded-3xl bg-gradient-to-r from-cyan-400 via-blue-500 to-cyan-300 text-xl font-bold text-slate-950 shadow-lg shadow-cyan-500/30 transition-all duration-300 hover:scale-[1.015] hover:shadow-xl hover:shadow-cyan-500/50 active:scale-[0.985]"
                    >
                      Send Reset Link
                    </button>
                  </form>

                  {/* Back to Login */}
                  <div className="mt-8 text-center">
                    <Link
                      href="/admin/login"
                      className="inline-flex items-center gap-2 text-lg font-medium text-cyan-400 hover:text-cyan-300 transition-colors"
                    >
                      <ArrowLeft className="h-5 w-5" />
                      Back to Login
                    </Link>
                  </div>
                </>
              ) : (
                /* Success State */
                <div className="py-12 text-center">
                  <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-full border border-green-500/30 bg-green-500/10">
                    <Mail className="h-10 w-10 text-green-400" />
                  </div>
                  <h3 className="text-3xl font-bold text-white">Check your email</h3>
                  <p className="mt-4 text-lg text-white/70 leading-relaxed">
                    We&apos;ve sent a password reset link to your email address.<br />
                    Please check your inbox (and spam folder).
                  </p>

                  <button
                    onClick={() => setIsSubmitted(false)}
                    className="mt-10 text-cyan-400 hover:text-cyan-300 font-medium transition-colors"
                  >
                    Send another reset link
                  </button>
                </div>
              )}

              {/* Footer Note */}
              <div className="mt-10 flex items-center justify-center gap-3 text-sm text-white/40">
                <div className="h-px flex-1 bg-white/10" />
                <span>Secure Password Recovery</span>
                <div className="h-px flex-1 bg-white/10" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
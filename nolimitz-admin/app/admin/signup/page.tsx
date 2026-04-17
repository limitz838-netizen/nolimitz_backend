"use client";

import Link from "next/link";
import { useState } from "react";
import {
  ShieldCheck,
  User,
  Mail,
  Lock,
  Eye,
  EyeOff,
  Phone,
  BadgeCheck,
} from "lucide-react";

export default function AdminSignupPage() {
  const [showPassword, setShowPassword] = useState(false);
  const [focusedField, setFocusedField] = useState<string | null>(null);

  const inputWrap = (name: string) =>
    `group flex h-16 items-center rounded-3xl border px-6 transition-all duration-300 ${
      focusedField === name
        ? "border-cyan-400/70 bg-white/10 shadow-[0_0_0_1px_rgba(103,232,249,0.2),0_0_35px_rgba(34,211,238,0.15)]"
        : "border-white/10 bg-white/6 hover:border-white/20"
    }`;

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    // TODO: Add form validation + signup logic here
    console.log("Admin signup submitted");
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
              <h2 className="text-4xl font-bold tracking-tight text-white sm:text-5xl">
                Create account
              </h2>

              <p className="mt-4 text-lg leading-relaxed text-white/70">
                Register as an administrator. Your account will require approval before gaining access to the dashboard.
              </p>

              <form onSubmit={handleSubmit} className="mt-10 space-y-7">
                {/* Full Name */}
                <div className="space-y-2.5">
                  <label className="text-sm font-medium text-white/90">Full Name</label>
                  <div className={inputWrap("full_name")}>
                    <User className="mr-4 h-5 w-5 text-cyan-300/80" />
                    <input
                      type="text"
                      required
                      placeholder="John Doe"
                      className="h-full flex-1 bg-transparent text-lg text-white outline-none placeholder:text-white/40"
                      onFocus={() => setFocusedField("full_name")}
                      onBlur={() => setFocusedField(null)}
                    />
                  </div>
                </div>

                {/* Display Name */}
                <div className="space-y-2.5">
                  <label className="text-sm font-medium text-white/90">Display Name</label>
                  <div className={inputWrap("display_name")}>
                    <BadgeCheck className="mr-4 h-5 w-5 text-cyan-300/80" />
                    <input
                      type="text"
                      required
                      placeholder="johndoe_admin"
                      className="h-full flex-1 bg-transparent text-lg text-white outline-none placeholder:text-white/40"
                      onFocus={() => setFocusedField("display_name")}
                      onBlur={() => setFocusedField(null)}
                    />
                  </div>
                </div>

                {/* Phone Number */}
                <div className="space-y-2.5">
                  <label className="text-sm font-medium text-white/90">Phone Number</label>
                  <div className={inputWrap("phone")}>
                    <Phone className="mr-4 h-5 w-5 text-cyan-300/80" />
                    <input
                      type="tel"
                      required
                      placeholder="+254 712 345 678"
                      className="h-full flex-1 bg-transparent text-lg text-white outline-none placeholder:text-white/40"
                      onFocus={() => setFocusedField("phone")}
                      onBlur={() => setFocusedField(null)}
                    />
                  </div>
                </div>

                {/* Email */}
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

                {/* Password */}
                <div className="space-y-2.5">
                  <label className="text-sm font-medium text-white/90">Password</label>
                  <div className={inputWrap("password")}>
                    <Lock className="mr-4 h-5 w-5 text-cyan-300/80" />
                    <input
                      type={showPassword ? "text" : "password"}
                      required
                      placeholder="Create a strong password"
                      className="h-full flex-1 bg-transparent text-lg text-white outline-none placeholder:text-white/40"
                      onFocus={() => setFocusedField("password")}
                      onBlur={() => setFocusedField(null)}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="ml-2 text-white/60 hover:text-cyan-300 transition-colors"
                      aria-label={showPassword ? "Hide password" : "Show password"}
                    >
                      {showPassword ? (
                        <EyeOff className="h-5 w-5" />
                      ) : (
                        <Eye className="h-5 w-5" />
                      )}
                    </button>
                  </div>
                </div>

                {/* Submit Button */}
                <button
                  type="submit"
                  className="mt-4 flex h-16 w-full items-center justify-center rounded-3xl bg-gradient-to-r from-cyan-400 via-blue-500 to-cyan-300 text-xl font-bold text-slate-950 shadow-lg shadow-cyan-500/30 transition-all duration-300 hover:scale-[1.015] hover:shadow-xl hover:shadow-cyan-500/50 active:scale-[0.985]"
                >
                  Create Admin Account
                </button>
              </form>

              {/* Sign In Link */}
              <div className="mt-8 text-center">
                <p className="text-lg text-white/60">
                  Already have an account?{" "}
                  <Link
                    href="/admin/login"
                    className="font-semibold text-cyan-400 hover:text-cyan-300 transition-colors"
                  >
                    Sign In
                  </Link>
                </p>
              </div>

              {/* Footer Note */}
              <div className="mt-10 flex items-center justify-center gap-3 text-sm text-white/40">
                <div className="h-px flex-1 bg-white/10" />
                <span>Admin approval is required after registration</span>
                <div className="h-px flex-1 bg-white/10" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
"use client";

import { ArrowLeft, Download, Smartphone } from "lucide-react";
import { useRouter } from "next/navigation";

export default function DownloadPage() {
  const router = useRouter();

  return (
    <main className="min-h-screen bg-[#020817] text-white">
      <div className="relative min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top,rgba(56,189,248,0.16),transparent_28%),radial-gradient(circle_at_bottom_right,rgba(59,130,246,0.14),transparent_26%),linear-gradient(180deg,#020817_0%,#07152b_52%,#0b1f44_100%)]">
        <div className="relative mx-auto max-w-5xl px-4 py-6 sm:px-6">
          <button
            onClick={() => router.push("/")}
            className="mb-6 inline-flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.05] px-4 py-2.5 text-sm font-medium text-white/80 transition hover:bg-white/10"
          >
            <ArrowLeft className="h-4 w-4" />
            Back Home
          </button>

          <div className="rounded-[34px] border border-white/10 bg-white/[0.06] p-8 shadow-[0_25px_80px_rgba(2,8,23,0.40)] backdrop-blur-2xl sm:p-10">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-cyan-300/20 bg-cyan-400/10">
              <Smartphone className="h-7 w-7 text-cyan-300" />
            </div>

            <h1 className="mt-5 text-4xl font-black tracking-tight text-white/95">
              Download NolimitzBots App
            </h1>

            <p className="mt-4 max-w-2xl text-base leading-8 text-white/62">
              Download the client application to activate your license, connect your
              trading account, and manage your robot or signals from your mobile device.
            </p>

            <div className="mt-8 grid gap-5 md:grid-cols-2">
              <div className="rounded-[28px] border border-white/10 bg-white/[0.04] p-6">
                <p className="text-xs uppercase tracking-[0.2em] text-white/35">Android</p>
                <h2 className="mt-3 text-2xl font-bold text-white/92">Android App</h2>
                <p className="mt-3 text-sm leading-7 text-white/56">
                  Download the latest Android build of NolimitzBots and start your setup.
                </p>
                <button className="mt-5 inline-flex items-center gap-2 rounded-2xl bg-gradient-to-r from-cyan-400 to-blue-500 px-5 py-3 text-sm font-semibold text-slate-950">
                  <Download className="h-4 w-4" />
                  Download APK
                </button>
              </div>

              <div className="rounded-[28px] border border-white/10 bg-white/[0.04] p-6">
                <p className="text-xs uppercase tracking-[0.2em] text-white/35">iPhone</p>
                <h2 className="mt-3 text-2xl font-bold text-white/92">iOS Version</h2>
                <p className="mt-3 text-sm leading-7 text-white/56">
                  iPhone support is coming soon. We will announce it once the release is live.
                </p>
                <button className="mt-5 rounded-2xl border border-white/10 bg-white/[0.05] px-5 py-3 text-sm font-semibold text-white/55">
                  Coming Soon
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
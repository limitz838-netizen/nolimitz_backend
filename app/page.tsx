"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import {
  FaAndroid,
  FaApple,
  FaWhatsapp,
  FaTelegramPlane,
} from "react-icons/fa";
import { Menu, X, LogIn, UserPlus } from "lucide-react";

export default function HomePage() {
  const [menuOpen, setMenuOpen] = useState(false);

  const handleIosClick = () => {
    alert("iOS version coming soon.");
  };

  return (
    <main className="min-h-screen bg-white text-slate-900">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-slate-200/70 bg-white/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 sm:px-6">
          <a
            href="/"
            className="text-[28px] font-black tracking-tight sm:text-[32px]"
            style={{ fontFamily: "Georgia, serif" }}
          >
            <span className="text-slate-900">Nolimitz</span>
            <span className="bg-gradient-to-r from-cyan-500 to-blue-600 bg-clip-text text-transparent">
              Bots
            </span>
          </a>

          <div className="hidden items-center gap-3 md:flex">
            <a
              href="/admin/login"
              className="rounded-2xl border border-cyan-200/70 bg-gradient-to-r from-cyan-400/80 to-blue-500/80 px-4 py-2 text-sm font-semibold text-white shadow-[0_10px_25px_rgba(59,130,246,0.16)] backdrop-blur-md transition hover:scale-[1.02]"
            >
              Admin Login
            </a>
            <a
              href="/admin/signup"
              className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-800 shadow-sm transition hover:bg-slate-50"
            >
              Admin Sign Up
            </a>
          </div>

          <button
            onClick={() => setMenuOpen((prev) => !prev)}
            className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm md:hidden"
            aria-label="Menu"
          >
            {menuOpen ? <X size={22} /> : <Menu size={22} />}
          </button>
        </div>

        {/* Mobile menu */}
        {menuOpen && (
          <div className="border-t border-slate-200 bg-white px-4 py-4 md:hidden">
            <div className="space-y-3">
              <div className="rounded-[24px] border border-cyan-200/70 bg-gradient-to-r from-cyan-400/15 to-blue-500/15 p-4 shadow-sm">
                <a
                  href="/admin/login"
                  className="flex items-center justify-between rounded-2xl bg-gradient-to-r from-cyan-400 to-blue-500 px-4 py-3 font-semibold text-white"
                >
                  <span className="flex items-center gap-2">
                    <LogIn size={18} />
                    Admin Login
                  </span>
                  <span>→</span>
                </a>
              </div>

              <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-4 shadow-sm">
                <a
                  href="/admin/signup"
                  className="flex items-center justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3 font-semibold text-slate-800"
                >
                  <span className="flex items-center gap-2">
                    <UserPlus size={18} />
                    Admin Sign Up
                  </span>
                  <span>→</span>
                </a>
              </div>
            </div>
          </div>
        )}
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden px-4 py-24 sm:px-6 sm:py-28">
        <div className="absolute inset-0 -z-10">
          <div className="absolute left-1/2 top-10 h-72 w-72 -translate-x-1/2 rounded-full bg-cyan-100 blur-3xl" />
          <div className="absolute right-10 top-20 h-56 w-56 rounded-full bg-blue-100 blur-3xl" />
        </div>

        <div className="mx-auto max-w-4xl text-center">
          <motion.h1
            initial={{ opacity: 0, y: 28 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7 }}
            className="text-4xl font-black leading-tight tracking-tight text-slate-900 sm:text-5xl md:text-6xl"
            style={{ fontFamily: "Georgia, serif" }}
          >
            Welcome to{" "}
            <span className="bg-gradient-to-r from-cyan-500 to-blue-600 bg-clip-text text-transparent">
              NolimitzBots
            </span>
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 28 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.85, delay: 0.1 }}
            className="mx-auto mt-6 max-w-2xl text-lg leading-8 text-slate-600 sm:text-xl"
          >
            Take full advantage of Artificial Intelligence directly on your mobile
            phone. Simple, fast, and powerful automation.
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 28 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.95, delay: 0.2 }}
            className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row"
          >
            <a
              href="/nolimitzbots.apk"
              download
              className="inline-flex min-w-[185px] items-center justify-center gap-2 rounded-2xl border border-cyan-200/70 bg-gradient-to-r from-cyan-400/85 to-blue-500/85 px-4 py-3 text-sm font-semibold text-white shadow-[0_10px_30px_rgba(59,130,246,0.18)] backdrop-blur-md transition hover:scale-[1.02]"
            >
              <FaAndroid size={18} />
              Android Download
            </a>

            <button
              onClick={handleIosClick}
              className="inline-flex min-w-[185px] items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-800 shadow-sm transition hover:bg-slate-50"
            >
              <FaApple size={18} />
              iOS
            </button>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 28 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 1.05, delay: 0.3 }}
            className="mt-6 flex items-center justify-center gap-5"
          >
            <a
              href="https://t.me/nolimitzbots"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 text-sm font-semibold text-blue-600 transition hover:text-blue-700"
            >
              <FaTelegramPlane size={18} />
              Telegram
            </a>
          </motion.div>
        </div>
      </section>

      {/* How it works */}
      <section className="bg-slate-50 px-4 py-20 sm:px-6">
        <div className="mx-auto max-w-5xl">
          <motion.div
            initial={{ opacity: 0, y: 26 }}
            whileInView={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.65 }}
            viewport={{ once: true }}
            className="text-center"
          >
            <h2
              className="text-3xl font-black tracking-tight text-slate-900 sm:text-4xl"
              style={{ fontFamily: "Georgia, serif" }}
            >
              How the Platform Works
            </h2>
            <p className="mx-auto mt-4 max-w-2xl text-base leading-7 text-slate-600 sm:text-lg">
              The process is simple, smooth, and built for mobile-first automation.
            </p>
          </motion.div>

          <div className="mt-12 grid gap-6 md:grid-cols-3">
            {[
              {
                step: "01",
                title: "Download the App",
                desc: "Install the NolimitzBots Android app on your phone and open it to begin setup.",
              },
              {
                step: "02",
                title: "Get a License Key",
                desc: "Receive your license key from an admin registered on the NolimitzBots platform.",
              },
              {
                step: "03",
                title: "Activate Your Account",
                desc: "Enter your license key, connect your account, and start using the platform directly from mobile.",
              },
            ].map((item, index) => (
              <motion.div
                key={item.step}
                initial={{ opacity: 0, y: 26 }}
                whileInView={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.65, delay: index * 0.12 }}
                viewport={{ once: true }}
                className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-[0_12px_35px_rgba(15,23,42,0.06)]"
              >
                <div className="inline-flex rounded-2xl bg-gradient-to-r from-cyan-400 to-blue-500 px-4 py-2 text-sm font-bold text-white shadow-sm">
                  {item.step}
                </div>
                <h3 className="mt-5 text-xl font-bold text-slate-900">
                  {item.title}
                </h3>
                <p className="mt-3 text-sm leading-7 text-slate-600">
                  {item.desc}
                </p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-500">
        © {new Date().getFullYear()} NolimitzBots. All rights reserved.
      </footer>

      {/* WhatsApp floating */}
      <a
        href="https://wa.me/message/CHJOXBZPYKEYG1"
        target="_blank"
        rel="noopener noreferrer"
        className="fixed bottom-5 right-5 z-50 inline-flex h-14 w-14 items-center justify-center rounded-full bg-green-500 text-white shadow-[0_10px_25px_rgba(34,197,94,0.35)] transition hover:scale-110"
        aria-label="WhatsApp Support"
      >
        <FaWhatsapp size={24} />
      </a>
    </main>
  );
}
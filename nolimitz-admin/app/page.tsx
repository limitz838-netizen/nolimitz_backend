"use client";

import { useState } from "react";
import { Download, Menu, X, Smartphone, Apple, Zap, Shield, Users, MessageCircle } from "lucide-react";

export default function HomePage() {
  const [menuOpen, setMenuOpen] = useState(false);

  const apkDownloadUrl = "/downloads/nolimitzbots.apk";

  return (
    <main className="min-h-screen bg-[#020817] text-white">
      {/* Header */}
      <header className="border-b border-white/10 sticky top-0 bg-[#020817]/90 backdrop-blur-lg z-50">
        <div className="max-w-6xl mx-auto flex justify-between items-center px-4 py-5">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 bg-cyan-400 rounded-2xl flex items-center justify-center">
              <span className="text-black font-bold text-2xl">N</span>
            </div>
            <div>
              <h1 className="text-2xl font-bold">Nolimitz<span className="text-cyan-400">Bots</span></h1>
              <p className="text-xs text-white/50">Mobile Automation Platform</p>
            </div>
          </div>

          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="md:hidden p-3 rounded-xl bg-white/5 border border-white/10"
          >
            {menuOpen ? <X size={24} /> : <Menu size={24} />}
          </button>

          <nav className="hidden md:flex items-center gap-8 text-sm">
            <a href="#how" className="hover:text-cyan-400 transition">How it Works</a>
            <a href="#contact" className="hover:text-cyan-400 transition">Contact</a>
          </nav>

          <a
            href={apkDownloadUrl}
            download="NolimitzBots.apk"
            className="hidden md:flex items-center gap-2 px-6 py-3 rounded-2xl bg-gradient-to-r from-cyan-400 to-blue-500 text-black font-semibold hover:scale-105 transition"
          >
            <Smartphone size={20} />
            Download Android
          </a>
        </div>

        {/* Mobile Menu */}
        {menuOpen && (
          <div className="md:hidden px-4 pb-6 border-t border-white/10">
            <div className="bg-[#0a1428] rounded-2xl p-6 space-y-4 text-center">
              <a href="#how" className="block py-3 text-lg">How it Works</a>
              <a href="#contact" className="block py-3 text-lg">Contact Us</a>

              <a
                href={apkDownloadUrl}
                download="NolimitzBots.apk"
                className="block w-full py-4 bg-cyan-400 text-black rounded-2xl font-semibold mt-4"
              >
                Download Android APK
              </a>
            </div>
          </div>
        )}
      </header>

      {/* Hero Section */}
      <section className="pt-20 pb-24 px-4 text-center max-w-4xl mx-auto">
        <h2 className="text-5xl md:text-6xl font-bold leading-tight">
          Move from PC to Mobile<br />
          with <span className="text-cyan-400">Powerful AI</span>
        </h2>

        <p className="mt-6 text-xl text-white/70 max-w-2xl mx-auto">
          Take full advantage of Artificial Intelligence directly on your mobile phone. 
          Simple, fast, and powerful automation — no computer needed.
        </p>

        <div className="mt-12 flex flex-col sm:flex-row gap-4 justify-center">
          <a
            href={apkDownloadUrl}
            download="NolimitzBots.apk"
            className="flex items-center justify-center gap-3 px-10 py-4 bg-gradient-to-r from-cyan-400 to-blue-500 text-black font-semibold rounded-2xl text-lg hover:scale-105 transition"
          >
            <Smartphone size={26} />
            Download Android App
          </a>

          <button
            onClick={() => alert("iOS version coming soon 🚀")}
            className="flex items-center justify-center gap-3 px-10 py-4 border border-white/30 rounded-2xl text-lg hover:bg-white/5 transition"
          >
            <Apple size={26} />
            iOS (Coming Soon)
          </button>
        </div>

        <p className="mt-8 text-sm text-white/50">
          App size: ~84 MB • Instant setup
        </p>
      </section>

      {/* How It Works */}
      <section id="how" className="py-20 bg-black/40">
        <div className="max-w-4xl mx-auto px-4">
          <div className="text-center mb-12">
            <h3 className="text-4xl font-bold mb-3">How It Works</h3>
            <p className="text-white/60 text-lg">Simple 4-step process</p>
          </div>

          <div className="space-y-16">
            {[
              {
                step: "1",
                title: "Download & Install",
                desc: "Download the NolimitzBots app on your Android phone and install it in seconds."
              },
              {
                step: "2",
                title: "Get Your License Key",
                desc: "You must have a license key. You will receive the license key from your mentor who is registered on our platform."
              },
              {
                step: "3",
                title: "Activate & Connect",
                desc: "Enter your license key and connect your account. The AI is now ready to work for you."
              },
              {
                step: "4",
                title: "Run on Mobile",
                desc: "Enjoy full automation and AI capabilities directly from your phone — anywhere, anytime."
              }
            ].map((item, i) => (
              <div key={i} className="flex gap-6 items-start">
                <div className="w-12 h-12 flex-shrink-0 rounded-2xl bg-cyan-400/10 border border-cyan-400/30 flex items-center justify-center text-2xl font-bold text-cyan-400">
                  {item.step}
                </div>
                <div>
                  <h4 className="text-2xl font-semibold mb-2">{item.title}</h4>
                  <p className="text-white/70 leading-relaxed">{item.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Benefits */}
      <section className="py-20 px-4">
        <div className="max-w-5xl mx-auto grid md:grid-cols-3 gap-8">
          <div className="text-center">
            <Zap className="mx-auto text-cyan-400 mb-4" size={48} />
            <h4 className="text-xl font-semibold mb-2">AI on Your Phone</h4>
            <p className="text-white/60">Harness the power of Artificial Intelligence without needing a computer.</p>
          </div>
          <div className="text-center">
            <Smartphone className="mx-auto text-cyan-400 mb-4" size={48} />
            <h4 className="text-xl font-semibold mb-2">Mobile Freedom</h4>
            <p className="text-white/60">Shift completely from PC to your mobile device. Work from anywhere.</p>
          </div>
          <div className="text-center">
            <Shield className="mx-auto text-cyan-400 mb-4" size={48} />
            <h4 className="text-xl font-semibold mb-2">Secure & Simple</h4>
            <p className="text-white/60">Easy activation with license key. Clean and user-friendly interface.</p>
          </div>
        </div>
      </section>

      {/* Contact */}
      <section id="contact" className="py-20 bg-black/40">
        <div className="max-w-2xl mx-auto text-center px-4">
          <h3 className="text-3xl font-bold mb-8">Get In Touch</h3>
          
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <a
              href="https://wa.me/254712345678" 
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center justify-center gap-3 bg-green-600 hover:bg-green-500 transition py-4 px-8 rounded-2xl font-medium"
            >
              <MessageCircle size={24} />
              Chat on WhatsApp
            </a>

            <a
              href="mailto:support@nolimitzbots.com"
              className="flex items-center justify-center gap-3 border border-white/30 hover:bg-white/5 transition py-4 px-8 rounded-2xl font-medium"
            >
              📧 Email Support
            </a>
          </div>

          <p className="mt-10 text-white/50 text-sm">
            Have questions? Our team is ready to help you get started.
          </p>
        </div>
      </section>

      {/* Final CTA */}
      <section className="py-20 text-center px-4">
        <div className="max-w-md mx-auto">
          <h3 className="text-3xl font-bold mb-4">Ready to Go Mobile?</h3>
          <p className="text-white/60 mb-8">
            Download the app and start using AI power directly on your phone today.
          </p>

          <a
            href={apkDownloadUrl}
            download="NolimitzBots.apk"
            className="inline-flex items-center gap-3 px-12 py-5 bg-cyan-400 hover:bg-cyan-300 text-black rounded-3xl font-semibold text-xl transition-all active:scale-95"
          >
            Download NolimitzBots
            <Download size={28} />
          </a>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-white/10 py-12 text-center text-sm text-white/40">
        <p>© 2026 NolimitzBots. All rights reserved.</p>
        <p className="mt-2">Mobile AI Automation Made Simple</p>
      </footer>
    </main>
  );
}
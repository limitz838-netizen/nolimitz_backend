"use client";

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  Check,
  Copy,
  Link2,
  Lock,
  MoreVertical,
  Plus,
  Power,
  Save,
  X,
} from "lucide-react";
import { getAdminToken, getApiBaseUrl } from "@/lib/admin-auth";

type ToastType = "success" | "error";

type ToastState = {
  show: boolean;
  type: ToastType;
  message: string;
};

type EASymbolItem = string | { id?: number; symbol_name?: string; symbol?: string; name?: string };

type LinkedEA = {
  id?: number;
  ea_name?: string;
  name?: string;
  ea_code?: string;
  code?: string;
};

type EADetail = {
  id: number;
  name?: string;
  ea_name?: string;
  code?: string;
  ea_code?: string;
  is_active?: boolean;
  active?: boolean;
  allowed_symbols?: EASymbolItem[];
  symbols?: EASymbolItem[];
};

const modernFont = {
  fontFamily: 'Geist Sans, Satoshi, "Public Sans", "Mona Sans", Inter, system-ui, sans-serif',
};

function normalizeSymbols(raw: EASymbolItem[] | undefined): string[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => {
      if (typeof item === "string") return item.trim().toUpperCase();
      const value = item?.symbol_name || item?.symbol || item?.name || "";
      return String(value).trim().toUpperCase();
    })
    .filter(Boolean);
}

export default function EADetailPage() {
  const router = useRouter();
  const params = useParams();
  const eaId = String(params?.eaId || "");

  const [loading, setLoading] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [savingSymbols, setSavingSymbols] = useState(false);
  const [linking, setLinking] = useState(false);

  const [ea, setEa] = useState<EADetail | null>(null);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [newSymbol, setNewSymbol] = useState("");
  const [linkCode, setLinkCode] = useState("");
  const [linkedEAs, setLinkedEAs] = useState<LinkedEA[]>([]);
  const [toast, setToast] = useState<ToastState>({
    show: false,
    type: "success",
    message: "",
  });

  const menuRef = useRef<HTMLDivElement>(null);

  const token = getAdminToken();
  const baseUrl = getApiBaseUrl();

  const eaName = useMemo(() => ea?.ea_name || ea?.name || "Unnamed EA", [ea]);
  const eaCode = useMemo(() => ea?.ea_code || ea?.code || "No code", [ea]);
  const isActive = useMemo(() => Boolean(ea?.is_active ?? ea?.active), [ea]);

  const showToast = useCallback((type: ToastType, message: string) => {
    setToast({ show: true, type, message });
    setTimeout(() => setToast((prev) => ({ ...prev, show: false })), 2800);
  }, []);

  const loadEA = useCallback(async () => {
    if (!eaId || !token) return;
    try {
      setLoading(true);
      const res = await fetch(`${baseUrl}/eas/${eaId}`, {
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (!res.ok) throw new Error();
      const data: EADetail = await res.json();
      setEa(data);
      setSymbols(normalizeSymbols(data.allowed_symbols || data.symbols));
    } catch {
      showToast("error", "Failed to load EA details.");
    } finally {
      setLoading(false);
    }
  }, [eaId, token, baseUrl, showToast]);

  const loadLinkedEAs = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch(`${baseUrl}/eas/links/mine`, {
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (res.ok) {
        const data = await res.json();
        setLinkedEAs(Array.isArray(data) ? data : []);
      }
    } catch {
      setLinkedEAs([]);
    }
  }, [token, baseUrl]);

  useEffect(() => {
    if (!token) {
      router.replace("/admin/login");
      return;
    }
    if (eaId) {
      loadEA();
      loadLinkedEAs();
    }
  }, [eaId, token, router, loadEA, loadLinkedEAs]);

  // Close menu on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(eaCode);
      setCopied(true);
      showToast("success", "EA code copied.");
      setTimeout(() => setCopied(false), 1600);
    } catch {
      showToast("error", "Failed to copy code.");
    }
  };

  const handleToggleStatus = async () => {
    if (!eaId) return;
    try {
      setToggling(true);
      const endpoint = isActive ? `${baseUrl}/eas/${eaId}/deactivate` : `${baseUrl}/eas/${eaId}/activate`;
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        await loadEA();
        showToast("success", isActive ? "EA deactivated." : "EA activated.");
      }
    } catch {
      showToast("error", "Failed to update status.");
    } finally {
      setToggling(false);
    }
  };

  const handleAddSymbol = () => {
    const clean = newSymbol.trim().toUpperCase();
    if (!clean || symbols.includes(clean)) {
      if (symbols.includes(clean)) showToast("error", "Symbol already added.");
      setNewSymbol("");
      return;
    }
    setSymbols((prev) => [...prev, clean]);
    setNewSymbol("");
  };

  const handleRemoveSymbol = (symbolToRemove: string) => {
    setSymbols((prev) => prev.filter((s) => s !== symbolToRemove));
  };

  const handleSaveSymbols = async () => {
    try {
      setSavingSymbols(true);
      const res = await fetch(`${baseUrl}/eas/${eaId}/symbols`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ symbols }),
      });
      if (res.ok) {
        await loadEA();
        showToast("success", "Symbols saved successfully.");
      }
    } catch {
      showToast("error", "Failed to save symbols.");
    } finally {
      setSavingSymbols(false);
    }
  };

  const handleLinkEA = async () => {
    const cleanCode = linkCode.trim();
    if (!cleanCode) return showToast("error", "Enter EA code first.");

    try {
      setLinking(true);
      const res = await fetch(`${baseUrl}/eas/link/by-code`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ code: cleanCode }),
      });
      if (res.ok) {
        setLinkCode("");
        await loadLinkedEAs();
        showToast("success", "EA linked successfully.");
      }
    } catch {
      showToast("error", "Failed to link EA.");
    } finally {
      setLinking(false);
    }
  };

  if (loading) {
    return (
      <main className="min-h-screen bg-[#020817] text-white" style={modernFont}>
        <div className="flex min-h-screen items-center justify-center">
          <div className="rounded-3xl border border-white/10 bg-white/[0.06] px-8 py-5 text-white/70 backdrop-blur-xl">
            Loading EA details...
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#020817] text-white" style={modernFont}>
      <div className="relative min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top,rgba(56,189,248,0.14),transparent_28%),radial-gradient(circle_at_bottom_right,rgba(59,130,246,0.12),transparent_26%),linear-gradient(180deg,#020817_0%,#07152b_52%,#0b1f44_100%)]">
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="absolute left-[-120px] top-12 h-72 w-72 rounded-full bg-cyan-400/10 blur-3xl" />
          <div className="absolute right-[-110px] top-36 h-80 w-80 rounded-full bg-blue-500/10 blur-3xl" />
          <div className="absolute bottom-0 left-1/2 h-72 w-72 -translate-x-1/2 rounded-full bg-violet-500/10 blur-3xl" />
        </div>

        <div className="relative mx-auto max-w-4xl px-4 py-6 sm:px-6">
          {toast.show && (
            <div className={`mb-6 rounded-2xl border px-5 py-3.5 text-sm font-medium shadow-[0_10px_30px_rgba(2,8,23,0.25)] backdrop-blur-xl ${
              toast.type === "success" ? "border-emerald-300/20 bg-emerald-400/10 text-emerald-200" : "border-red-300/20 bg-red-400/10 text-red-200"
            }`}>
              {toast.message}
            </div>
          )}

          {/* Navigation - Simplified */}
          <div className="mb-6 flex items-center justify-between rounded-3xl border border-white/10 bg-white/[0.05] px-5 py-3.5 backdrop-blur-xl">
            <button
              onClick={() => router.push("/admin/manage-eas")}
              className="inline-flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.05] px-4 py-2 text-sm font-medium text-white/75 hover:bg-white/[0.08]"
            >
              <ArrowLeft className="h-4 w-4" />
              Back
            </button>

            <div className="relative" ref={menuRef}>
              <button
                onClick={() => setMenuOpen((prev) => !prev)}
                className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-cyan-300/20 bg-white/[0.05] text-cyan-300 hover:bg-white/[0.08]"
              >
                <MoreVertical className="h-5 w-5" />
              </button>
              {/* Menu content remains the same */}
              {menuOpen && (
                <div className="absolute right-0 top-14 z-50 w-72 rounded-3xl border border-white/10 bg-[#0b1730]/95 p-2 shadow-2xl backdrop-blur-2xl">
                  <button onClick={() => { setMenuOpen(false); document.getElementById("link-section")?.scrollIntoView({ behavior: "smooth" }); }}
                    className="flex w-full items-center gap-3 rounded-2xl px-4 py-3 hover:bg-white/[0.06]">
                    <Link2 className="h-4 w-4 text-cyan-300" /> Link EA by Code
                  </button>
                  <button onClick={() => { setMenuOpen(false); document.getElementById("linked-eas")?.scrollIntoView({ behavior: "smooth" }); }}
                    className="flex w-full items-center gap-3 rounded-2xl px-4 py-3 hover:bg-white/[0.06]">
                    <Link2 className="h-4 w-4 text-cyan-300" /> My Linked EAs
                  </button>
                </div>
              )}
            </div>
          </div>

          <div className="space-y-6">
            {/* === EA HEADER - Green Card like screenshot === */}
            <section className="rounded-3xl bg-gradient-to-br from-emerald-700 via-emerald-800 to-emerald-900 p-6 shadow-xl">
              <div className="flex items-center gap-2">
                <div className="h-2 w-2 rounded-full bg-emerald-400" />
                <span className="text-xs font-semibold uppercase tracking-widest text-emerald-300">EXPERT ADVISOR</span>
              </div>

              <h1 className="mt-3 text-3xl font-bold tracking-tight text-white">{eaName}</h1>

              <div className="mt-4 inline-flex items-center gap-2 rounded-full border border-amber-400/30 bg-amber-400/10 px-4 py-1 text-xs font-medium text-amber-300">
                <Lock className="h-3.5 w-3.5" />
                Keep code private
              </div>

              {/* EA Code Card */}
              <div className="mt-6 rounded-2xl border border-white/10 bg-black/30 p-5">
                <div className="text-xs uppercase tracking-widest text-emerald-300/70">EA CODE</div>
                <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <code className="font-mono text-[17px] font-medium tracking-wider text-white break-all">
                    {eaCode}
                  </code>
                  <button
                    onClick={handleCopy}
                    className="inline-flex h-9 items-center gap-2 rounded-xl border border-emerald-400/30 bg-emerald-400/10 px-5 text-xs font-semibold text-emerald-200 hover:bg-emerald-400/20"
                  >
                    {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                    {copied ? "COPIED" : "COPY"}
                  </button>
                </div>
              </div>

              <p className="mt-6 text-[13px] leading-relaxed text-emerald-100/80">
                This is the EA code for <span className="font-semibold text-white">{eaName}</span>. 
                Do not share this secret code with anyone — it authenticates your EA with the licensing server.
              </p>

              <button
                onClick={handleToggleStatus}
                disabled={toggling}
                className={`mt-6 h-10 w-full rounded-2xl border text-sm font-semibold transition disabled:opacity-60 ${
                  isActive
                    ? "border-red-400/30 bg-red-400/10 text-red-200 hover:bg-red-400/15"
                    : "border-emerald-400/30 bg-emerald-400/10 text-emerald-200 hover:bg-emerald-400/15"
                }`}
              >
                <Power className="mr-2 inline h-4 w-4" />
                {toggling ? "Updating..." : isActive ? "Deactivate" : "Activate"}
              </button>
            </section>

            {/* === SYMBOLS SECTION - Exact match to screenshot === */}
            <section className="overflow-hidden rounded-3xl border border-white/10 bg-white/[0.05] shadow-xl backdrop-blur-2xl">
              <div className="px-6 py-6">
                <h2 className="text-2xl font-bold tracking-tight">Symbols</h2>
                <p className="mt-1 text-sm text-white/60">Click a symbol to add it to {eaName}</p>

                <div className="mt-4 inline-flex items-center rounded-full bg-emerald-400/10 px-5 py-1.5 text-sm font-medium text-emerald-300">
                  {symbols.length} symbols active
                </div>
              </div>

              {/* Custom Add */}
              <div className="border-t border-white/10 px-6 py-5">
                <div className="flex items-center gap-3">
                  <span className="text-sm font-medium text-white/70">Custom:</span>
                  <input
                    value={newSymbol}
                    onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
                    onKeyDown={(e) => e.key === "Enter" && handleAddSymbol()}
                    placeholder="e.g. USDMXN"
                    className="flex-1 rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3 text-sm placeholder:text-white/40 focus:border-emerald-400/30"
                  />
                  <button
                    onClick={handleAddSymbol}
                    className="h-11 rounded-2xl bg-emerald-500 px-6 text-sm font-semibold text-black hover:bg-emerald-400 transition"
                  >
                    Add
                  </button>
                </div>
              </div>

              {/* Active Symbols List */}
              <div className="border-t border-white/10">
                <div className="flex items-center justify-between border-b border-white/10 px-6 py-4">
                  <span className="font-semibold">Active Symbols</span>
                  <span className="text-sm text-white/40">{symbols.length} added</span>
                </div>

                {symbols.length > 0 ? (
                  symbols.map((symbol) => (
                    <div key={symbol} className="flex items-center justify-between border-b border-white/10 px-6 py-4 last:border-none">
                      <div className="flex items-center gap-3">
                        <div className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
                        <span className="text-base font-medium text-white/90">{symbol}</span>
                      </div>
                      <button
                        onClick={() => handleRemoveSymbol(symbol)}
                        className="rounded-xl border border-red-400/20 bg-red-400/5 px-5 py-1.5 text-sm font-medium text-red-200 hover:bg-red-400/10"
                      >
                        Remove
                      </button>
                    </div>
                  ))
                ) : (
                  <div className="px-6 py-12 text-center text-sm text-white/40">No symbols added yet.</div>
                )}
              </div>

              <div className="border-t border-white/10 px-6 py-6">
                <button
                  onClick={handleSaveSymbols}
                  disabled={savingSymbols}
                  className="h-10 w-full rounded-2xl border border-cyan-300/20 bg-cyan-400/10 text-sm font-semibold text-cyan-200 hover:bg-cyan-400/15 disabled:opacity-60"
                >
                  <Save className="mr-2 inline h-4 w-4" />
                  {savingSymbols ? "Saving..." : "Save Symbols"}
                </button>
              </div>
            </section>

            {/* Link EA & Linked EAs sections remain unchanged */}
            <section id="link-section" className="rounded-[28px] border border-white/10 bg-white/[0.06] p-6 shadow-xl backdrop-blur-2xl">
              <h2 className="text-2xl font-bold tracking-tight text-white/92">Link EA by Code</h2>
              <p className="mt-1 text-sm text-white/50">Connect this Expert Advisor to other supported EA links.</p>
              <div className="mt-6 flex gap-3">
                <input
                  value={linkCode}
                  onChange={(e) => setLinkCode(e.target.value)}
                  placeholder="Enter EA code to link"
                  className="h-12 flex-1 rounded-2xl border border-white/10 bg-white/[0.04] px-5 text-sm placeholder:text-white/40 focus:border-cyan-300/30"
                />
                <button
                  onClick={handleLinkEA}
                  disabled={linking}
                  className="h-12 rounded-2xl border border-cyan-300/20 bg-cyan-400/10 px-8 text-sm font-semibold text-cyan-300 hover:bg-cyan-400/15 disabled:opacity-60"
                >
                  <Link2 className="mr-2 h-4 w-4" />
                  {linking ? "Linking..." : "Link"}
                </button>
              </div>
            </section>

            <section id="linked-eas" className="rounded-[28px] border border-white/10 bg-white/[0.06] p-6 shadow-xl backdrop-blur-2xl">
              <h2 className="text-2xl font-bold tracking-tight text-white/92">My Linked EAs</h2>
              <p className="mt-1 text-sm text-white/50">Linked Expert Advisors connected to your account.</p>

              <div className="mt-6 space-y-3">
                {linkedEAs.length > 0 ? (
                  linkedEAs.map((item, i) => (
                    <div key={item.id ?? i} className="rounded-2xl border border-white/10 bg-white/[0.04] px-6 py-5">
                      <p className="font-semibold text-white/90">{item.ea_name || item.name || "Linked EA"}</p>
                      <p className="mt-1 text-sm text-white/50">Code: {item.ea_code || item.code || "N/A"}</p>
                    </div>
                  ))
                ) : (
                  <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.02] py-12 text-center text-white/40">
                    No linked EAs yet.
                  </div>
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </main>
  );
}
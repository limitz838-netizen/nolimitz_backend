"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  Check,
  ChevronRight,
  Circle,
} from "lucide-react";
import { getAdminToken, getApiBaseUrl } from "@/lib/admin-auth";

type EAItem = {
  id: number;
  name?: string;
  ea_name?: string;
  is_active?: boolean;
  active?: boolean;
};

type CreateEAResponse = {
  id?: number;
  ea_id?: number;
  name?: string;
  ea_name?: string;
};

type ToastType = "success" | "error";

type ToastState = {
  show: boolean;
  type: ToastType;
  message: string;
};

export default function ManageEAsPage() {
  const router = useRouter();

  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [eas, setEAs] = useState<EAItem[]>([]);
  const [toast, setToast] = useState<ToastState>({
    show: false,
    type: "success",
    message: "",
  });

  const [eaName, setEaName] = useState("");
  const [version, setVersion] = useState("v1.0");
  const [isShareable, setIsShareable] = useState(true);
  const [confirmed, setConfirmed] = useState(false);

  const token = getAdminToken();
  const baseUrl = getApiBaseUrl();

  const modernFont = {
    fontFamily:
      '"Geist Sans","Satoshi","Public Sans","Mona Sans",Inter,ui-sans-serif,system-ui,sans-serif',
  };

  const activeCount = useMemo(() => {
  return eas.filter((item) => Boolean(item.is_active ?? item.active)).length;
}, [eas]);

const showToast = (type: ToastType, message: string) => {
  setToast({ show: true, type, message });
  window.setTimeout(() => {
    setToast((prev) => ({ ...prev, show: false }));
  }, 2400);
};

const loadEAs = async () => {
  try {
    setLoading(true);

    const res = await fetch(`${baseUrl}/eas/`, {
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      cache: "no-store",
    });

    if (!res.ok) {
      throw new Error("Failed to load EAs");
    }

    const data = await res.json();
    setEAs(Array.isArray(data) ? data : []);
  } catch (error) {
    console.error(error);
    showToast("error", "Failed to load Expert Advisors.");
    setEAs([]);
  } finally {
    setLoading(false);
  }
};

useEffect(() => {
  if (!token) {
    router.replace("/admin/login");
    return;
  }

  loadEAs();
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, []);

const resetForm = () => {
  setEaName("");
  setVersion("v1.0");
  setIsShareable(true);
  setConfirmed(false);
};

const handleCreateEA = async () => {
  const cleanName = eaName.trim();
  const cleanVersion = version.trim() || "v1.0";

  if (!cleanName) {
    showToast("error", "Enter EA name first.");
    return;
  }

  if (!confirmed) {
    showToast("error", "Please confirm that you are an admin.");
    return;
  }

  try {
    setCreating(true);

    const generatedCodeName = `${cleanName
      .toUpperCase()
      .replace(/[^A-Z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")}-${cleanVersion
      .toUpperCase()
      .replace(/[^A-Z0-9]+/g, "")}`;

    const payload = {
      name: cleanName,
      code_name: generatedCodeName,
      version: cleanVersion,
      is_shareable: isShareable,
    };

    const res = await fetch(`${baseUrl}/eas/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const errText = await res.text();
      throw new Error(errText || "Failed to create EA");
    }

    const data: CreateEAResponse = await res.json();
    const newId = data.id ?? data.ea_id;

    showToast("success", "EA created successfully.");
    resetForm();

    if (newId) {
      router.push(`/admin/manage-eas/${newId}`);
      return;
    }

    await loadEAs();
  } catch (error) {
    console.error(error);
    showToast("error", "Failed to create EA.");
  } finally {
    setCreating(false);
  }
};
  return (
    <main
      className="min-h-screen bg-[#020817] text-white"
      style={modernFont}
    >
      <div className="relative min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top,rgba(56,189,248,0.16),transparent_28%),radial-gradient(circle_at_bottom_right,rgba(59,130,246,0.14),transparent_26%),linear-gradient(180deg,#020817_0%,#07152b_52%,#0b1f44_100%)]">
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="absolute left-[-120px] top-12 h-72 w-72 rounded-full bg-cyan-400/10 blur-3xl" />
          <div className="absolute right-[-110px] top-36 h-80 w-80 rounded-full bg-blue-500/10 blur-3xl" />
          <div className="absolute bottom-0 left-1/2 h-72 w-72 -translate-x-1/2 rounded-full bg-violet-500/10 blur-3xl" />
        </div>

        <div className="relative mx-auto w-full max-w-3xl px-4 py-4 sm:px-6 sm:py-6">
          <div className="mb-4 flex items-center justify-between rounded-[24px] border border-white/10 bg-white/[0.05] px-4 py-3 backdrop-blur-xl">
            <button
              type="button"
              onClick={() => router.push("/admin/dashboard")}
              className="inline-flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.05] px-3 py-2 text-sm font-medium text-white/78 transition hover:bg-white/[0.08]"
            >
              <ArrowLeft className="h-4 w-4" />
              Back
            </button>

            <div className="text-right">
              <p className="text-[11px] uppercase tracking-[0.28em] text-white/32">
                Management
              </p>
              <p className="text-sm font-semibold text-white/84">Manage EAs</p>
            </div>
          </div>

          {toast.show && (
            <div
              className={`mb-4 rounded-2xl border px-4 py-3 text-sm font-medium shadow-[0_10px_30px_rgba(2,8,23,0.25)] backdrop-blur-xl transition-all ${
                toast.type === "success"
                  ? "border-emerald-300/20 bg-emerald-400/10 text-emerald-200"
                  : "border-red-300/20 bg-red-400/10 text-red-200"
              }`}
            >
              {toast.message}
            </div>
          )}

          <div className="space-y-4">
            <section className="rounded-[28px] border border-white/10 bg-white/[0.06] p-4 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl sm:p-5">
              <div className="mb-4">
                <p className="text-[11px] uppercase tracking-[0.28em] text-white/34">
                  Expert Advisors
                </p>
                <h1 className="mt-2 text-[28px] font-bold tracking-tight text-white/92 sm:text-[30px]">
                  New EA
                </h1>
                <p className="mt-1 text-sm leading-6 text-white/48">
                  Add a new Expert Advisor to licence and manage.
                </p>
              </div>

              <div className="space-y-4">
                <div>
                  <label className="mb-2 block text-[11px] font-semibold uppercase tracking-[0.24em] text-white/46">
                    EA Name
                  </label>
                  <input
                    value={eaName}
                    onChange={(e) => setEaName(e.target.value)}
                    placeholder="Full EA name including version"
                    className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.04] px-4 text-[15px] text-white outline-none placeholder:text-white/28 focus:border-cyan-300/25 focus:bg-white/[0.06]"
                  />
                </div>

                <div>
                  <label className="mb-2 block text-[11px] font-semibold uppercase tracking-[0.24em] text-white/46">
                    Version
                  </label>
                  <input
                    value={version}
                    onChange={(e) => setVersion(e.target.value)}
                    placeholder="v1.0"
                    className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.04] px-4 text-[15px] text-white outline-none placeholder:text-white/28 focus:border-cyan-300/25 focus:bg-white/[0.06]"
                  />
                </div>

                <div>
                  <label className="mb-2 block text-[11px] font-semibold uppercase tracking-[0.24em] text-white/46">
                    Shareable
                  </label>
                  <div className="grid grid-cols-2 gap-3">
                    <button
                      type="button"
                      onClick={() => setIsShareable(true)}
                      className={`flex h-11 items-center justify-center rounded-2xl border text-sm font-semibold transition ${
                        isShareable
                          ? "border-emerald-300/25 bg-emerald-400/14 text-emerald-200"
                          : "border-white/10 bg-white/[0.04] text-white/64 hover:bg-white/[0.06]"
                      }`}
                    >
                      Yes
                    </button>

                    <button
                      type="button"
                      onClick={() => setIsShareable(false)}
                      className={`flex h-11 items-center justify-center rounded-2xl border text-sm font-semibold transition ${
                        !isShareable
                          ? "border-emerald-300/25 bg-emerald-400/14 text-emerald-200"
                          : "border-white/10 bg-white/[0.04] text-white/64 hover:bg-white/[0.06]"
                      }`}
                    >
                      No
                    </button>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => setConfirmed((prev) => !prev)}
                  className={`flex w-full items-center gap-3 rounded-2xl border px-4 py-3 text-left transition ${
                    confirmed
                      ? "border-cyan-300/20 bg-cyan-400/10"
                      : "border-white/10 bg-white/[0.04] hover:bg-white/[0.06]"
                  }`}
                >
                  <div
                    className={`flex h-5 w-5 items-center justify-center rounded-md border ${
                      confirmed
                        ? "border-cyan-300/40 bg-cyan-400/14 text-cyan-200"
                        : "border-white/18 bg-transparent text-transparent"
                    }`}
                  >
                    <Check className="h-3.5 w-3.5" />
                  </div>

                  <span className="text-sm font-medium text-white/76">
                    I confirm that I am an admin
                  </span>
                </button>

                <div className="flex items-center gap-3 pt-1">
                  <button
                    type="button"
                    onClick={handleCreateEA}
                    disabled={creating}
                    className="inline-flex h-11 items-center justify-center rounded-2xl border border-cyan-300/20 bg-gradient-to-r from-cyan-400/80 via-sky-500/80 to-cyan-300/80 px-5 text-sm font-bold text-slate-950 shadow-[0_8px_24px_rgba(34,211,238,0.18)] transition hover:opacity-95 disabled:opacity-60"
                  >
                    {creating ? "Creating..." : "Create EA"}
                  </button>

                  <button
                    type="button"
                    onClick={resetForm}
                    className="inline-flex h-11 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.04] px-5 text-sm font-semibold text-white/70 transition hover:bg-white/[0.06]"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            </section>

            <section className="rounded-[28px] border border-white/10 bg-white/[0.06] p-4 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl sm:p-5">
              <div className="mb-4">
                <h2 className="text-[24px] font-bold tracking-tight text-white/92">
                  Your Expert Advisors
                </h2>
                <p className="mt-1 text-sm leading-6 text-white/46">
                  All EAs registered under your account.
                </p>
              </div>

              <div className="space-y-3">
                {loading ? (
                  <>
                    {[1, 2, 3].map((item) => (
                      <div
                        key={item}
                        className="animate-pulse rounded-[24px] border border-white/10 bg-white/[0.04] p-4"
                      >
                        <div className="h-5 w-32 rounded bg-white/10" />
                        <div className="mt-3 h-4 w-20 rounded bg-white/10" />
                      </div>
                    ))}
                  </>
                ) : eas.length === 0 ? (
                  <div className="rounded-[24px] border border-dashed border-white/10 bg-white/[0.03] px-4 py-5 text-sm text-white/42">
                    No Expert Advisors created yet.
                  </div>
                ) : (
                  eas.map((ea) => {
                    const name = ea.ea_name || ea.name || "Unnamed EA";
                    const active = Boolean(ea.is_active ?? ea.active);

                    return (
                      <button
                        key={ea.id}
                        type="button"
                        onClick={() => router.push(`/admin/manage-eas/${ea.id}`)}
                        className="group flex w-full items-center justify-between rounded-[24px] border border-white/10 bg-white/[0.04] px-4 py-4 text-left transition hover:border-cyan-300/16 hover:bg-white/[0.06]"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-lg font-semibold tracking-tight text-white/90">
                            {name}
                          </p>

                          <div className="mt-2 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs font-semibold text-white/58">
                            <Circle
                              className={`h-2.5 w-2.5 fill-current ${
                                active ? "text-emerald-300" : "text-red-300"
                              }`}
                            />
                            {active ? "Active" : "Inactive"}
                          </div>
                        </div>

                        <ChevronRight className="h-5 w-5 shrink-0 text-white/34 transition group-hover:translate-x-0.5 group-hover:text-cyan-300" />
                      </button>
                    );
                  })
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </main>
  );
}
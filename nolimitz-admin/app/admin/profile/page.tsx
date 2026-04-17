"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  Building2,
  Check,
  ImagePlus,
  Loader2,
  Mail,
  MessageCircle,
  Phone,
  Save,
  Send,
  Shield,
  Upload,
} from "lucide-react";
import { getAdminToken, getApiBaseUrl } from "@/lib/admin-auth";

type ToastType = "success" | "error";

type ToastState = {
  show: boolean;
  type: ToastType;
  message: string;
};

type AdminProfileResponse = {
  id?: number | string;
  admin_id?: string;
  full_name?: string;
  email?: string;
  phone?: string;
  company_name?: string;
  support_email?: string;
  telegram?: string;
  whatsapp?: string;
  logo_url?: string;
};

type LogoUploadResponse = {
  logo_url?: string;
  url?: string;
  file_url?: string;
};

const modernFont = {
  fontFamily:
    'Geist Sans, Satoshi, "Public Sans", "Mona Sans", Inter, system-ui, sans-serif',
};

export default function AdminProfilePage() {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const token = getAdminToken();
  const baseUrl = getApiBaseUrl();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploadingLogo, setUploadingLogo] = useState(false);

  const [fullName, setFullName] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [phone, setPhone] = useState("");
  const [supportEmail, setSupportEmail] = useState("");
  const [telegram, setTelegram] = useState("");
  const [whatsapp, setWhatsapp] = useState("");
  const [logoUrl, setLogoUrl] = useState("");

  const [toast, setToast] = useState<ToastState>({
    show: false,
    type: "success",
    message: "",
  });

  const showToast = (type: ToastType, message: string) => {
    setToast({ show: true, type, message });
    window.setTimeout(() => {
      setToast((prev) => ({ ...prev, show: false }));
    }, 2400);
  };

  const resolvedLogoUrl = useMemo(() => {
    if (!logoUrl) return "";
    if (logoUrl.startsWith("http://") || logoUrl.startsWith("https://")) {
      return logoUrl;
    }
    if (logoUrl.startsWith("/")) {
      return `${baseUrl}${logoUrl}`;
    }
    return `${baseUrl}/${logoUrl}`;
  }, [logoUrl, baseUrl]);

  const loadProfile = async () => {
    try {
      setLoading(true);

      const endpoints = [
        `${baseUrl}/admin/me`,
        `${baseUrl}/admin/profile`,
        `${baseUrl}/profile`,
      ];

      let data: AdminProfileResponse | null = null;

      for (const url of endpoints) {
        try {
          const res = await fetch(url, {
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${token}`,
            },
            cache: "no-store",
          });

          if (res.ok) {
            data = (await res.json()) as AdminProfileResponse;
            break;
          }
        } catch {
          // try next endpoint
        }
      }

      if (!data) {
        throw new Error("Failed to load profile");
      }

      setFullName(data.full_name || "");
      setCompanyName(data.company_name || "");
      setPhone(data.phone || "");
      setSupportEmail(data.support_email || data.email || "");
      setTelegram(data.telegram || "");
      setWhatsapp(data.whatsapp || "");
      setLogoUrl(data.logo_url || "");
    } catch (error) {
      console.error(error);
      showToast("error", "Failed to load profile.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!token) {
      router.replace("/admin/login");
      return;
    }

    loadProfile();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const handlePickLogo = () => {
    fileInputRef.current?.click();
  };

  const handleLogoSelected = async (
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      setUploadingLogo(true);

      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch(`${baseUrl}/admin/profile/logo`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
        body: formData,
      });

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(errText || "Failed to upload logo");
      }

      const data = (await res.json()) as LogoUploadResponse;
      const nextLogo =
        data.logo_url || data.url || data.file_url || "";

      if (!nextLogo) {
        throw new Error("Logo uploaded but no URL returned");
      }

      setLogoUrl(nextLogo);
      showToast("success", "Logo uploaded successfully.");
    } catch (error) {
      console.error(error);
      showToast("error", "Failed to upload logo.");
    } finally {
      setUploadingLogo(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  };

  const handleSaveProfile = async () => {
    try {
      setSaving(true);

      const payload = {
        full_name: fullName.trim(),
        company_name: companyName.trim(),
        phone: phone.trim(),
        support_email: supportEmail.trim().toLowerCase(),
        telegram: telegram.trim(),
        whatsapp: whatsapp.trim(),
      };

      const res = await fetch(`${baseUrl}/admin/profile`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(errText || "Failed to save profile");
      }

      showToast("success", "Profile saved successfully.");
      await loadProfile();
    } catch (error) {
      console.error(error);
      showToast("error", "Failed to save profile.");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <main
        className="min-h-screen bg-[#020817] text-white"
        style={modernFont}
      >
        <div className="relative min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top,rgba(56,189,248,0.16),transparent_28%),radial-gradient(circle_at_bottom_right,rgba(59,130,246,0.14),transparent_26%),linear-gradient(180deg,#020817_0%,#07152b_52%,#0b1f44_100%)]">
          <div className="relative mx-auto flex min-h-screen max-w-5xl items-center justify-center px-4 py-8">
            <div className="rounded-3xl border border-white/10 bg-white/[0.06] px-6 py-5 text-sm text-white/70 backdrop-blur-2xl">
              Loading profile settings...
            </div>
          </div>
        </div>
      </main>
    );
  }

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

        <div className="relative mx-auto max-w-5xl px-4 py-5 sm:px-6 lg:px-8 lg:py-8">
          {toast.show && (
            <div
              className={`mb-5 rounded-2xl border px-4 py-3 text-sm font-medium shadow-[0_10px_30px_rgba(2,8,23,0.25)] backdrop-blur-xl ${
                toast.type === "success"
                  ? "border-emerald-300/20 bg-emerald-400/10 text-emerald-200"
                  : "border-red-300/20 bg-red-400/10 text-red-200"
              }`}
            >
              {toast.message}
            </div>
          )}

          <div className="mb-5 flex items-center justify-between rounded-[28px] border border-white/10 bg-white/[0.05] px-4 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] backdrop-blur-xl sm:px-5">
            <button
              type="button"
              onClick={() => router.push("/admin/dashboard")}
              className="inline-flex h-11 items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm font-semibold text-white/80 transition hover:bg-white/[0.08]"
            >
              <ArrowLeft className="h-4 w-4" />
              Back to Dashboard
            </button>

            <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-cyan-300/20 bg-gradient-to-br from-sky-400/20 to-blue-500/10 text-cyan-300">
              <Shield className="h-5 w-5" />
            </div>
          </div>

          <div className="grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
            <section className="rounded-[30px] border border-white/10 bg-white/[0.06] p-5 shadow-[0_20px_60px_rgba(2,8,23,0.30)] backdrop-blur-2xl sm:p-6">
              <p className="text-xs uppercase tracking-[0.24em] text-white/35">
                Brand Identity
              </p>
              <h1 className="mt-3 text-3xl font-black tracking-tight text-white/92">
                Profile Settings
              </h1>
              <p className="mt-3 text-sm leading-7 text-white/56">
                Manage your company branding and support details shown across the platform.
              </p>

              <div className="mt-6 rounded-[26px] border border-white/10 bg-white/[0.04] p-5">
                <p className="text-xs uppercase tracking-[0.24em] text-white/35">
                  Logo / Brand Picture
                </p>

                <div className="mt-4 flex flex-col items-center gap-4">
                  <div className="relative flex h-36 w-36 items-center justify-center overflow-hidden rounded-[28px] border border-cyan-300/20 bg-gradient-to-br from-sky-400/15 to-blue-500/10 shadow-[0_0_30px_rgba(56,189,248,0.10)]">
                    {resolvedLogoUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={resolvedLogoUrl}
                        alt="Admin logo"
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <div className="flex flex-col items-center gap-2 text-white/45">
                        <Building2 className="h-8 w-8 text-cyan-300" />
                        <span className="text-xs font-medium">
                          No logo yet
                        </span>
                      </div>
                    )}

                    {uploadingLogo && (
                      <div className="absolute inset-0 flex items-center justify-center bg-black/45 backdrop-blur-sm">
                        <Loader2 className="h-6 w-6 animate-spin text-cyan-300" />
                      </div>
                    )}
                  </div>

                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    onChange={handleLogoSelected}
                    className="hidden"
                  />

                  <button
                    type="button"
                    onClick={handlePickLogo}
                    disabled={uploadingLogo}
                    className="inline-flex h-11 items-center gap-2 rounded-2xl border border-cyan-300/20 bg-cyan-400/10 px-4 text-sm font-semibold text-cyan-300 transition hover:bg-cyan-400/15 disabled:opacity-60"
                  >
                    {uploadingLogo ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Uploading...
                      </>
                    ) : (
                      <>
                        <ImagePlus className="h-4 w-4" />
                        Click to add from gallery
                      </>
                    )}
                  </button>
                </div>
              </div>

              <div className="mt-5 rounded-[26px] border border-white/10 bg-white/[0.04] p-5">
                <p className="text-xs uppercase tracking-[0.24em] text-white/35">
                  Preview
                </p>

                <div className="mt-4 rounded-[24px] border border-white/10 bg-[#081426] p-4">
                  <div className="flex items-center gap-3">
                    <div className="flex h-14 w-14 items-center justify-center overflow-hidden rounded-2xl border border-cyan-300/20 bg-white/[0.05]">
                      {resolvedLogoUrl ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={resolvedLogoUrl}
                          alt="Brand preview"
                          className="h-full w-full object-cover"
                        />
                      ) : (
                        <Shield className="h-6 w-6 text-cyan-300" />
                      )}
                    </div>

                    <div className="min-w-0">
                      <p className="truncate text-base font-bold text-white/90">
                        {companyName.trim() || "Your Brand Name"}
                      </p>
                      <p className="truncate text-sm text-white/50">
                        {supportEmail.trim() || "support@example.com"}
                      </p>
                    </div>
                  </div>

                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3">
                      <p className="text-xs uppercase tracking-[0.20em] text-white/35">
                        Support Phone
                      </p>
                      <p className="mt-2 text-sm text-white/82">
                        {phone.trim() || "Not added"}
                      </p>
                    </div>

                    <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3">
                      <p className="text-xs uppercase tracking-[0.20em] text-white/35">
                        WhatsApp
                      </p>
                      <p className="mt-2 text-sm text-white/82">
                        {whatsapp.trim() || "Not added"}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </section>

            <section className="rounded-[30px] border border-white/10 bg-white/[0.06] p-5 shadow-[0_20px_60px_rgba(2,8,23,0.30)] backdrop-blur-2xl sm:p-6">
              <p className="text-xs uppercase tracking-[0.24em] text-white/35">
                Contact & Support
              </p>
              <h2 className="mt-3 text-2xl font-black tracking-tight text-white/92">
                Admin Information
              </h2>
              <p className="mt-3 text-sm leading-7 text-white/56">
                These details can be used across admin branding, robot info, and support areas in the client app.
              </p>

              <div className="mt-6 grid gap-4">
                <div>
                  <label className="mb-2 block text-xs uppercase tracking-[0.22em] text-white/35">
                    Full Name
                  </label>
                  <input
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    placeholder="Your full name"
                    className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none placeholder:text-white/28 focus:border-cyan-300/25 focus:bg-white/[0.07]"
                  />
                </div>

                <div>
                  <label className="mb-2 block text-xs uppercase tracking-[0.22em] text-white/35">
                    Company / Brand Name
                  </label>
                  <input
                    value={companyName}
                    onChange={(e) => setCompanyName(e.target.value)}
                    placeholder="NolimitzBots"
                    className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none placeholder:text-white/28 focus:border-cyan-300/25 focus:bg-white/[0.07]"
                  />
                </div>

                <div>
                  <label className="mb-2 block text-xs uppercase tracking-[0.22em] text-white/35">
                    Phone
                  </label>
                  <div className="relative">
                    <Phone className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-cyan-300/80" />
                    <input
                      value={phone}
                      onChange={(e) => setPhone(e.target.value)}
                      placeholder="+254..."
                      className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] pl-11 pr-4 text-sm text-white outline-none placeholder:text-white/28 focus:border-cyan-300/25 focus:bg-white/[0.07]"
                    />
                  </div>
                </div>

                <div>
                  <label className="mb-2 block text-xs uppercase tracking-[0.22em] text-white/35">
                    Support Email
                  </label>
                  <div className="relative">
                    <Mail className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-cyan-300/80" />
                    <input
                      value={supportEmail}
                      onChange={(e) => setSupportEmail(e.target.value)}
                      placeholder="support@example.com"
                      className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] pl-11 pr-4 text-sm text-white outline-none placeholder:text-white/28 focus:border-cyan-300/25 focus:bg-white/[0.07]"
                    />
                  </div>
                </div>

                <div>
                  <label className="mb-2 block text-xs uppercase tracking-[0.22em] text-white/35">
                    Telegram
                  </label>
                  <div className="relative">
                    <Send className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-cyan-300/80" />
                    <input
                      value={telegram}
                      onChange={(e) => setTelegram(e.target.value)}
                      placeholder="@yourtelegram"
                      className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] pl-11 pr-4 text-sm text-white outline-none placeholder:text-white/28 focus:border-cyan-300/25 focus:bg-white/[0.07]"
                    />
                  </div>
                </div>

                <div>
                  <label className="mb-2 block text-xs uppercase tracking-[0.22em] text-white/35">
                    WhatsApp
                  </label>
                  <div className="relative">
                    <MessageCircle className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-cyan-300/80" />
                    <input
                      value={whatsapp}
                      onChange={(e) => setWhatsapp(e.target.value)}
                      placeholder="+254..."
                      className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] pl-11 pr-4 text-sm text-white outline-none placeholder:text-white/28 focus:border-cyan-300/25 focus:bg-white/[0.07]"
                    />
                  </div>
                </div>
              </div>

              <div className="mt-6 flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={handleSaveProfile}
                  disabled={saving}
                  className="inline-flex h-12 items-center justify-center gap-2 rounded-2xl border border-cyan-300/20 bg-gradient-to-r from-cyan-400/90 to-blue-500/90 px-5 text-sm font-bold text-slate-950 transition hover:brightness-105 disabled:opacity-60"
                >
                  {saving ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Saving...
                    </>
                  ) : (
                    <>
                      <Save className="h-4 w-4" />
                      Save Changes
                    </>
                  )}
                </button>

                <button
                  type="button"
                  onClick={loadProfile}
                  disabled={saving || uploadingLogo}
                  className="inline-flex h-12 items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/[0.05] px-5 text-sm font-semibold text-white/78 transition hover:bg-white/[0.08] disabled:opacity-60"
                >
                  <Upload className="h-4 w-4" />
                  Reload
                </button>
              </div>

              <div className="mt-6 rounded-[24px] border border-white/10 bg-white/[0.04] p-4">
                <p className="text-xs uppercase tracking-[0.22em] text-white/35">
                  Important
                </p>
                <p className="mt-3 text-sm leading-7 text-white/56">
                  Robot name should come from the Expert Advisor name created in Manage EAs.
                  Company / Brand Name here is for branding, logo, support identity, and contacts only.
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <span className="rounded-full border border-cyan-300/20 bg-cyan-400/10 px-3 py-1 text-xs font-semibold text-cyan-300">
                    Robot name = EA name
                  </span>
                  <span className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-xs text-white/70">
                    Brand name = company_name
                  </span>
                </div>
              </div>
            </section>
          </div>
        </div>
      </div>
    </main>
  );
}
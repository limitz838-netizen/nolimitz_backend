"use client";

import { useEffect, useMemo, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  Bot,
  CalendarDays,
  Gauge,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Menu,
  Power,
  RefreshCcw,
  Shield,
  UserSquare2,
  Users,
  X,
  Bell,
} from "lucide-react";
import {
  getAdminToken,
  getApiBaseUrl,
  removeAdminToken,
} from "@/lib/admin-auth";

type MenuKey =
  | "dashboard"
  | "generate"
  | "manage-eas"
  | "stats"
  | "push-signals"
  | "control-bot"
  | "users"
  | "licenses"
  | "reactivate"
  | "profile"
  | "logout";

type MenuItem = {
  key: MenuKey;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
};

type AdminProfile = {
  id?: number | string;
  admin_id?: string;
  full_name?: string;
  display_name?: string;
  email?: string;
  phone?: string;
  logo_url?: string;
  is_approved?: boolean;
  is_active?: boolean;
  company_name?: string;
};

type DashboardStats = {
  active_subscriptions: number;
  total_license_key_users: number;
  total_eas: number;
};

type LicenseItem = {
  id?: number | string;
  license_id?: number | string;
  client_name?: string;
  client_email?: string;
  email?: string;
  ea_name?: string;
  robot_name?: string;
  license_key?: string;
  key?: string;
  duration?: string | number;
  duration_days?: string | number;
  is_active?: boolean;
  active?: boolean;
};

type EAItem = {
  id?: number | string;
  name?: string;
  ea_name?: string;
  is_active?: boolean;
  active?: boolean;
};

const menuItems: MenuItem[] = [
  { key: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { key: "generate", label: "Generate License Key", icon: KeyRound },
  { key: "manage-eas", label: "Manage EAs", icon: Bot },
  { key: "stats", label: "Key Stats", icon: Gauge },
  { key: "push-signals", label: "Push Signals", icon: Bell },
  { key: "control-bot", label: "Control Bot", icon: Power },
  { key: "users", label: "My Users", icon: Users },
  { key: "licenses", label: "Licenses", icon: Shield },
  { key: "reactivate", label: "Re-Activate Key", icon: RefreshCcw },
  { key: "profile", label: "Profile Settings", icon: UserSquare2 },
  { key: "logout", label: "Logout", icon: LogOut },
];

async function fetchWithFallback<T>(
  paths: string[],
  token: string,
  baseUrl: string
): Promise<T | null> {
  for (const path of paths) {
    try {
      const res = await fetch(`${baseUrl}${path}`, {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        cache: "no-store",
      });

      if (res.ok) {
        return (await res.json()) as T;
      }
    } catch (err) {
      console.warn(`Failed to fetch ${path}:`, err);
    }
  }
  return null;
}

function safeText(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined) return fallback;
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return String(value);
  }
  return fallback;
}

export default function AdminDashboardPage() {
  const router = useRouter();
  const baseUrl = getApiBaseUrl();

  const [menuOpen, setMenuOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<MenuKey>("dashboard");
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [loadingData, setLoadingData] = useState(true);
  const [copiedId, setCopiedId] = useState<number | string | null>(null);

  const [licenses, setLicenses] = useState<LicenseItem[]>([]);
  const [eas, setEAs] = useState<EAItem[]>([]);
  const [adminProfile, setAdminProfile] = useState<AdminProfile | null>(null);

  const [licenseSearch, setLicenseSearch] = useState("");
  const [userSearch, setUserSearch] = useState("");

  const [reactivateSearch, setReactivateSearch] = useState("");
  const [reactivateResult, setReactivateResult] = useState<LicenseItem | null>(null);
  const [reactivateLoading, setReactivateLoading] = useState(false);
  const [reactivating, setReactivating] = useState(false);
  const [resetSuccess, setResetSuccess] = useState(false);
  
  const [licenseEA, setLicenseEA] = useState("");
  const [clientName, setClientName] = useState("");
  const [clientEmail, setClientEmail] = useState("");
  const [duration, setDuration] = useState("30days");
  const [creatingLicense, setCreatingLicense] = useState(false);
  const [licenseMessage, setLicenseMessage] = useState("");

  const [generatedLicense, setGeneratedLicense] = useState<any | null>(null);
  const [revealLicenseKey, setRevealLicenseKey] = useState(false);
  const [copiedLicenseKey, setCopiedLicenseKey] = useState(false);

  const [signalEA, setSignalEA] = useState("");
  const [signalSymbol, setSignalSymbol] = useState("");
  const [signalAction, setSignalAction] = useState("buy");
  const [signalSL, setSignalSL] = useState("");
  const [signalTP, setSignalTP] = useState("");
  const [signalComment, setSignalComment] = useState("");
  const [sendingSignal, setSendingSignal] = useState(false);
  const [signalMessage, setSignalMessage] = useState("");

  const [masterEA, setMasterEA] = useState("");
  const [masterLogin, setMasterLogin] = useState("");
  const [masterPassword, setMasterPassword] = useState("");
  const [masterServer, setMasterServer] = useState("");
  const [masterStatus, setMasterStatus] = useState("Not connected");
  const [masterAccountName, setMasterAccountName] = useState("");
  const [masterBrokerName, setMasterBrokerName] = useState("");
  const [savingMaster, setSavingMaster] = useState(false);
  const [verifyingMaster, setVerifyingMaster] = useState(false);
  const [masterMessage, setMasterMessage] = useState("");

  const [stats, setStats] = useState<DashboardStats>({
    active_subscriptions: 0,
    total_license_key_users: 0,
    total_eas: 0,
  });
  
  const [robotCreateJson, setRobotCreateJson] = useState(
  '{\n  "ea_id": 1,\n  "symbol": "XAUUSD",\n  "action": "buy",\n  "sl": "0",\n  "tp": "0",\n  "comment": "manual robot trade"\n}'
  );

  const [robotFeedJson, setRobotFeedJson] = useState(
  '{\n  "license_key": ""\n}'
  );

  const [copierOpenJson, setCopierOpenJson] = useState(
  '{\n  "ea_code": "",\n  "master_ticket": "",\n  "symbol": "XAUUSD",\n  "action": "buy",\n  "sl": "0",\n  "tp": "0",\n  "price": "0",\n  "comment": ""\n}'
  );

  const [copierModifyJson, setCopierModifyJson] = useState(
  '{\n  "master_ticket": "",\n  "sl": "0",\n  "tp": "0",\n  "price": "0"\n}'
  );

  const [copierCloseJson, setCopierCloseJson] = useState(
  '{\n  "master_ticket": "",\n  "comment": "close trade"\n}'
  );

  const [claimExecutionsJson, setClaimExecutionsJson] = useState("{}");

  const [controlLoading, setControlLoading] = useState(false);
  const [controlMessage, setControlMessage] = useState("");
  const [controlResponse, setControlResponse] = useState<any>(null);

  const today = useMemo(() => {
    return new Date().toLocaleDateString("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  }, []);

  const adminName = useMemo(() => {
    if (adminProfile?.full_name?.trim()) {
      return adminProfile.full_name.trim().split(" ")[0];
    }
    if (adminProfile?.display_name?.trim()) {
      return adminProfile.display_name.trim();
    }
    if (adminProfile?.email?.trim()) {
      return adminProfile.email.trim().split("@")[0];
    }
    return "Admin";
  }, [adminProfile]);

  const adminId = useMemo(() => {
    if (adminProfile?.admin_id) return String(adminProfile.admin_id);
    if (adminProfile?.id) return String(adminProfile.id);
    return "000";
  }, [adminProfile]);

  const filteredLicenses = useMemo(() => {
    const q = licenseSearch.trim().toLowerCase();

    if (!q) return licenses;

    return licenses.filter((item) => {
      const clientName = safeText(item.client_name, "").toLowerCase();
      const clientEmail = safeText(item.client_email || item.email, "").toLowerCase();
      const eaName = safeText(item.ea_name || item.robot_name, "").toLowerCase();
      const key = safeText(item.license_key || item.key, "").toLowerCase();
      const duration = safeText(item.duration || item.duration_days, "").toLowerCase();

      return (
        clientName.includes(q) ||
        clientEmail.includes(q) ||
        eaName.includes(q) ||
        key.includes(q) ||
        duration.includes(q)
      );
    });
  }, [licenses, licenseSearch]);

  const groupedUsers = useMemo(() => {
    const grouped: Record<
      string,
      {
        name: string;
        email: string;
        licenses: LicenseItem[];
        activeCount: number;
        inactiveCount: number;
        eas: string[];
      }
    > = {};

    licenses.forEach((item) => {
      const email = safeText(item.client_email || item.email, "")
        .trim()
        .toLowerCase();
      const name = safeText(item.client_name, "Unknown User").trim();
      const groupKey = email || name.toLowerCase();

      if (!grouped[groupKey]) {
        grouped[groupKey] = {
          name,
          email: email || "No email",
          licenses: [],
          activeCount: 0,
          inactiveCount: 0,
          eas: [],
        };
      }

      grouped[groupKey].licenses.push(item);

      if (item.is_active ?? item.active) {
        grouped[groupKey].activeCount += 1;
      } else {
        grouped[groupKey].inactiveCount += 1;
      }

      const eaName = safeText(item.ea_name || item.robot_name, "").trim();
      if (eaName && !grouped[groupKey].eas.includes(eaName)) {
        grouped[groupKey].eas.push(eaName);
      }
    });

    return Object.values(grouped);
  }, [licenses]);

  const filteredUsers = useMemo(() => {
    const q = userSearch.trim().toLowerCase();

    if (!q) return groupedUsers;

    return groupedUsers.filter((user) => {
      return (
        user.name.toLowerCase().includes(q) ||
        user.email.toLowerCase().includes(q) ||
        user.eas.some((ea) => ea.toLowerCase().includes(q))
      );
    });
  }, [groupedUsers, userSearch]);

  const calculateStats = useCallback(
    (licenseList: LicenseItem[], eaList: EAItem[]) => {
      const activeLicenses = licenseList.filter((item) =>
        Boolean(item.is_active ?? item.active)
      ).length;

      const uniqueUsers = new Set(
        licenseList.map((item) =>
          safeText(
            item.client_email || item.email || item.client_name || "unknown",
            ""
          )
            .trim()
            .toLowerCase()
        )
      ).size;

      setStats({
        active_subscriptions: activeLicenses,
        total_license_key_users: uniqueUsers,
        total_eas: eaList.length,
      });
    },
    []
  );

  const fetchAdminProfile = useCallback(async () => {
    try {
      const token = getAdminToken();
      if (!token) return;

      const profile = await fetchWithFallback<AdminProfile>(
        ["/admin/me", "/admin/profile", "/profile"],
        token,
        baseUrl
      );

      if (profile) {
        setAdminProfile(profile);
      }
    } catch (err) {
      console.warn("Failed to fetch admin profile", err);
    }
  }, [baseUrl]);

  const fetchLicenses = useCallback(async () => {
    try {
      const token = getAdminToken();
      if (!token) return;

      const data = await fetchWithFallback<LicenseItem[]>(
        ["/licenses"],
        token,
        baseUrl
      );

      const safeLicenses = Array.isArray(data) ? data : [];
      setLicenses(safeLicenses);

      setStats((prev) => ({
        ...prev,
        active_subscriptions: safeLicenses.filter((item) =>
          Boolean(item.is_active ?? item.active)
        ).length,
        total_license_key_users: new Set(
          safeLicenses.map((item) =>
            safeText(
              item.client_email || item.email || item.client_name || "unknown",
              ""
            )
              .trim()
              .toLowerCase()
          )
        ).size,
      }));
    } catch (err) {
      console.warn("Failed to fetch licenses", err);
      setLicenses([]);
    }
  }, [baseUrl]);

  const fetchEAs = useCallback(async () => {
    try {
      const token = getAdminToken();
      if (!token) return;

      const data = await fetchWithFallback<EAItem[]>(
        ["/eas"],
        token,
        baseUrl
      );

      const safeEAs = Array.isArray(data) ? data : [];
      setEAs(safeEAs);

      setStats((prev) => ({
        ...prev,
        total_eas: safeEAs.length,
      }));
    } catch (err) {
      console.warn("Failed to fetch EAs", err);
      setEAs([]);
    }
  }, [baseUrl]);

  const initializeDashboard = useCallback(async () => {
    const token = getAdminToken();

    if (!token) {
      router.replace("/admin/login");
      return;
    }

    setCheckingAuth(false);
    setLoadingData(true);

    try {
      const profile = await fetchWithFallback<AdminProfile>(
        ["/admin/me", "/admin/profile", "/profile"],
        token,
        baseUrl
      );

      if (!profile) {
        removeAdminToken();
        router.replace("/admin/login");
        return;
      }

      setAdminProfile(profile);

      const [licenseData, eaData] = await Promise.all([
        fetchWithFallback<LicenseItem[]>(
          ["/licenses/", "/licenses", "/admin/licenses"],
          token,
          baseUrl
        ),
        fetchWithFallback<EAItem[]>(
          ["/eas/", "/eas", "/admin/eas"],
          token,
          baseUrl
        ),
      ]);

      const safeLicenses = Array.isArray(licenseData) ? licenseData : [];
      const safeEAs = Array.isArray(eaData) ? eaData : [];

      setLicenses(safeLicenses);
      setEAs(safeEAs);
      calculateStats(safeLicenses, safeEAs);
    } catch (error) {
      console.error("Dashboard bootstrap error:", error);
    } finally {
      setLoadingData(false);
    }
  }, [router, baseUrl, calculateStats]);

  useEffect(() => {
    initializeDashboard();
  }, [initializeDashboard]);

  const handlePushSignal = useCallback(async () => {
    if (!signalEA || !signalSymbol.trim() || !signalAction.trim()) {
      setSignalMessage("Please fill EA, symbol and action.");
      return;
    }

    try {
      setSendingSignal(true);
      setSignalMessage("");

      const token = getAdminToken();
      const payload = {
        ea_id: Number(signalEA),
        symbol: signalSymbol.trim(),
        action: signalAction.trim(),
        sl: signalSL.trim(),
        tp: signalTP.trim(),
        comment: signalComment.trim(),
      };

      const res = await fetch(`${baseUrl}/signals/push`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
      });

      const data = await res.json().catch(() => null);

      if (!res.ok) {
        throw new Error(data?.detail || "Failed to push signal");
      }

      setSignalMessage("Signal pushed successfully.");
      setSignalSymbol("");
      setSignalSL("");
      setSignalTP("");
      setSignalComment("");
    } catch (err: any) {
      console.warn("Failed to push signal", err);
      setSignalMessage(err?.message || "Failed to push signal.");
    } finally {
      setSendingSignal(false);
    }
  }, [
    baseUrl,
    signalEA,
    signalSymbol,
    signalAction,
    signalSL,
    signalTP,
    signalComment,
  ]);

  useEffect(() => {
  const loadMasterAccount = async () => {
    try {
      const token = getAdminToken();
      const res = await fetch(`${baseUrl}/admin/master-account`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      const data = await res.json();

      if (data?.ea_id) setMasterEA(String(data.ea_id));
      if (data?.mt_login) setMasterLogin(data.mt_login);
      if (data?.mt_server) setMasterServer(data.mt_server);
      if (data?.account_name) setMasterAccountName(data.account_name);
      if (data?.broker_name) setMasterBrokerName(data.broker_name);

      setMasterStatus(data?.connected ? "Connected" : data?.mt_login ? "Saved" : "Not connected");
    } catch (err) {
      console.warn("Failed to load master account", err);
    }
  };

  loadMasterAccount();
}, [baseUrl]);

  const handleCreateLicense = useCallback(async () => {
  if (!licenseEA || !clientName.trim() || !clientEmail.trim()) {
    setLicenseMessage("Please fill all fields.");
    return;
  }

  try {
    setCreatingLicense(true);
    setLicenseMessage("");
    setGeneratedLicense(null);
    setRevealLicenseKey(false);
    setCopiedLicenseKey(false);

    const token = getAdminToken();

    const res = await fetch(`${baseUrl}/licenses/generate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        ea_id: Number(licenseEA),
        client_name: clientName.trim(),
        client_email: clientEmail.trim(),
        duration: duration,
      }),
    });

    const data = await res.json().catch(() => null);

    if (!res.ok) {
      throw new Error(
        typeof data?.detail === "string"
          ? data.detail
          : JSON.stringify(data?.detail || data || "Failed to generate license")
     );
    }

    const license = data?.license || null;

    setGeneratedLicense(license);
    setLicenseMessage("License generated successfully.");

    setClientName("");
    setClientEmail("");
    setDuration("30days");

    await fetchLicenses();
  } catch (err: any) {
    console.warn(err);
    setLicenseMessage(err?.message || "Failed to generate license.");
  } finally {
    setCreatingLicense(false);
  }
}, [licenseEA, clientName, clientEmail, duration, baseUrl, fetchLicenses]);

const handleCopyGeneratedLicense = useCallback(async () => {
  const key = generatedLicense?.license_key;
  if (!key) return;

  try {
    await navigator.clipboard.writeText(key);
    setCopiedLicenseKey(true);
    setTimeout(() => setCopiedLicenseKey(false), 1500);
  } catch (err) {
    console.warn("Copy failed", err);
  }
}, [generatedLicense]);

  const handleControlAction = useCallback(
  async (endpoint: string, method: "GET" | "POST", bodyText?: string) => {
    try {
      setControlLoading(true);
      setControlMessage("");
      setControlResponse(null);

      const token = getAdminToken();
      const baseUrl = getApiBaseUrl();

      let parsedBody: any = undefined;

      if (method === "POST") {
        try {
          parsedBody = bodyText?.trim() ? JSON.parse(bodyText) : {};
        } catch {
          setControlMessage("Invalid JSON format.");
          setControlLoading(false);
          return;
        }
      }

      const res = await fetch(`${baseUrl}${endpoint}`, {
        method,
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        ...(method === "POST" ? { body: JSON.stringify(parsedBody) } : {}),
      });

      const data = await res.json().catch(() => null);

      if (!res.ok) {
        throw new Error(data?.detail || `Request failed: ${endpoint}`);
      }

      setControlMessage("Request completed successfully.");
      setControlResponse(data);
    } catch (err: any) {
      console.warn("Control action failed", err);
      setControlMessage(err?.message || "Request failed.");
      setControlResponse(null);
    } finally {
      setControlLoading(false);
    }
  },
  []
);

  const handleDeactivateLicense = useCallback(
    async (licenseId: number | string | undefined) => {
      if (!licenseId) return;

      try {
        const token = getAdminToken();

        const res = await fetch(`${baseUrl}/licenses/${licenseId}/deactivate`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
        });

        if (!res.ok) {
          throw new Error("Failed to deactivate license");
        }

        await fetchLicenses();
      } catch (err) {
        console.warn("Failed to deactivate license", err);
        alert("Failed to deactivate license");
      }
    },
    [baseUrl, fetchLicenses]
  );

  const handleSearchLicenseForReactivation = useCallback(async () => {
    const key = reactivateSearch.trim().toLowerCase();

    if (!key) {
      setReactivateResult(null);
      return;
    }

    setReactivateLoading(true);
    setResetSuccess(false);

    try {
      const token = getAdminToken();

      const data = await fetchWithFallback<LicenseItem[]>(
        ["/licenses/", "/licenses", "/admin/licenses"],
        token || "",
        baseUrl
      );

      const list = Array.isArray(data) ? data : [];

      const found =
        list.find((item) => {
          return (
            safeText(item.license_key || item.key, "")
              .trim()
              .toLowerCase() === key
          );
        }) || null;

      setReactivateResult(found);
    } catch (err) {
      console.warn("Failed to search license", err);
      setReactivateResult(null);
    } finally {
      setReactivateLoading(false);
    }
  }, [reactivateSearch, baseUrl]);

  const handleReactivateLicense = useCallback(async () => {
    if (!reactivateResult?.id && !reactivateResult?.license_id) return;

    try {
      setReactivating(true);

      const token = getAdminToken();
      const targetId = reactivateResult.id ?? reactivateResult.license_id;

      const res = await fetch(
        `${baseUrl}/admin/licenses/${targetId}/reset-device-lock`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
        }
      );

      if (!res.ok) {
        throw new Error("Failed to reset device lock");
      }

      const updated = await res.json().catch(() => null);

      setReactivateResult((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          ...(updated || {}),
        };
      });

      setResetSuccess(true);

      setTimeout(() => {
        setResetSuccess(false);
      }, 2500);

      await fetchLicenses();
    } catch (err) {
      console.warn("Failed to reset device lock", err);
      alert("Failed to reset device lock");
    } finally {
      setReactivating(false);
    }
  }, [reactivateResult, baseUrl, fetchLicenses]);

  const handleSaveMasterAccount = useCallback(async () => {
  if (!masterEA || !masterLogin || !masterPassword || !masterServer) {
    setMasterMessage("Please fill EA, login, password and server.");
    return;
  }

  try {
    setSavingMaster(true);
    setMasterMessage("");

    const token = getAdminToken();

    const res = await fetch(`${baseUrl}/admin/master-account/save`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        ea_id: Number(masterEA),
        mt_login: masterLogin.trim(),
        mt_password: masterPassword.trim(),
        mt_server: masterServer.trim(),
      }),
    });

    const data = await res.json().catch(() => null);

    if (!res.ok) {
      throw new Error(data?.detail || data?.message || "Failed to save master account.");
    }

    setMasterStatus("Saved");
    setMasterAccountName(data?.account_name || "");
    setMasterBrokerName(data?.broker_name || "");
    setMasterMessage(data?.message || "Master account saved successfully.");
  } catch (err: any) {
    console.warn("Failed to save master account", err);
    setMasterMessage(err?.message || "Failed to save master account.");
  } finally {
    setSavingMaster(false);
  }
}, [masterEA, masterLogin, masterPassword, masterServer, baseUrl]);

  const handleVerifyMasterAccount = useCallback(async () => {
  if (!masterEA || !masterLogin || !masterPassword || !masterServer) {
    setMasterMessage("Please fill EA, login, password and server.");
    return;
  }

  try {
    setVerifyingMaster(true);
    setMasterMessage("");
    const token = getAdminToken();

    const res = await fetch(`${baseUrl}/admin/master-account/connected`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        account_name: `Master ${masterLogin.trim()}`,
        broker_name: masterServer.trim(),
      }),
    });

    const data = await res.json().catch(() => null);

    if (!res.ok) {
      throw new Error(data?.detail || data?.message || "Verification failed.");
    }

    setMasterStatus("Connected");
    setMasterAccountName(data?.account_name || `Master ${masterLogin.trim()}`);
    setMasterBrokerName(data?.broker_name || masterServer.trim());
    setMasterMessage(data?.message || "Master account verified successfully.");
  } catch (err: any) {
    console.warn("Failed to verify master account", err);
    setMasterStatus("Connection failed");
    setMasterMessage(err?.message || "Failed to verify master account.");
  } finally {
    setVerifyingMaster(false);
  }
}, [masterEA, masterLogin, masterPassword, masterServer, baseUrl]);

  const handleLogout = useCallback(() => {
    removeAdminToken();
    router.replace("/admin/login");
  }, [router]);

  const handleMenuClick = useCallback(
    (item: MenuItem) => {
      setMenuOpen(false);

      switch (item.key) {
        case "logout":
          handleLogout();
          return;

        case "dashboard":
          setActiveTab("dashboard");
          router.push("/admin/dashboard");
          return;

        case "manage-eas":
          router.push("/admin/manage-eas");
          return;

       case "generate":
          setActiveTab("generate");
          return;

        case "profile":
          router.push("/admin/profile");
          return;

        case "stats":
        case "push-signals":
        case "control-bot":
        case "users":
        case "licenses":
        case "reactivate":
          setActiveTab(item.key);
          return;

        default:
          setActiveTab(item.key);
      }
    },
    [handleLogout, router]
  );

  if (checkingAuth) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[#020817] text-white">
        <div className="rounded-3xl border border-white/10 bg-white/5 px-8 py-6 text-sm text-white/70 backdrop-blur-xl">
          Loading dashboard...
        </div>
      </main>
    );
  }

  const durationLabelMap: Record<string, string> = {
  "30days": "30 Days",
  "1month": "1 Month",
  "1year": "1 Year",
  "lifetime": "Lifetime",
};

const selectedDurationLabel = durationLabelMap[duration] || duration;

const selectedEAName =
  eas.find((ea) => String(ea.id) === String(licenseEA))?.ea_name ||
  eas.find((ea) => String(ea.id) === String(licenseEA))?.name ||
  "Selected EA";

function formatDateValue(value?: string | null) {
  if (!value) return "Lifetime";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

  return (
    <main className="min-h-screen bg-[#020817] text-white">
      <div className="relative min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top,rgba(56,189,248,0.18),transparent_26%),radial-gradient(circle_at_bottom_right,rgba(59,130,246,0.16),transparent_24%),linear-gradient(180deg,#020817_0%,#07152b_52%,#0b1f44_100%)]">
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="absolute left-[-120px] top-12 h-72 w-72 rounded-full bg-cyan-400/10 blur-3xl" />
          <div className="absolute right-[-110px] top-36 h-80 w-80 rounded-full bg-blue-500/10 blur-3xl" />
          <div className="absolute bottom-0 left-1/2 h-72 w-72 -translate-x-1/2 rounded-full bg-violet-500/10 blur-3xl" />
        </div>

        <div className="relative mx-auto flex min-h-screen max-w-7xl">
          <aside
            className={`fixed inset-y-0 left-0 z-50 w-[84%] max-w-[320px] transform border-r border-white/10 bg-[#081426]/95 p-4 shadow-2xl backdrop-blur-2xl transition-transform duration-300 lg:static lg:z-auto lg:w-[290px] lg:translate-x-0 lg:border-r lg:bg-white/[0.04] ${
              menuOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
            }`}
          >
            <div className="flex h-full flex-col">
              <div className="flex items-center justify-between pb-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-cyan-300/20 bg-gradient-to-br from-sky-400/20 to-blue-500/10 shadow-[0_0_24px_rgba(56,189,248,0.12)]">
                    <Shield className="h-6 w-6 text-cyan-300" />
                  </div>
                  <div>
                    <p className="bg-gradient-to-r from-cyan-300 via-sky-400 to-blue-500 bg-clip-text text-lg font-black tracking-tight text-transparent">
                      NolimitzBots
                    </p>
                    <p className="text-xs font-medium text-white/45">Admin Portal</p>
                  </div>
                </div>

                <button
                  onClick={() => setMenuOpen(false)}
                  className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/10 bg-white/5 text-white/70 lg:hidden"
                  aria-label="Close menu"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto rounded-3xl border border-white/10 bg-white/[0.04] p-4">
                <p className="text-xs uppercase tracking-[0.24em] text-white/35">
                  Main Menu
                </p>

                <div className="mt-4 space-y-2 pb-4">
                  {menuItems.map((item) => {
                    const Icon = item.icon;
                    const isActive = activeTab === item.key;

                    return (
                      <button
                        key={item.key}
                        onClick={() => handleMenuClick(item)}
                        className={`group flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-left transition-all ${
                          isActive
                            ? "border border-cyan-300/20 bg-gradient-to-r from-blue-500/30 to-violet-500/15 shadow-[0_0_20px_rgba(59,130,246,0.16)]"
                            : "border border-transparent hover:border-white/10 hover:bg-white/[0.05]"
                        } ${item.key === "logout" ? "hover:border-red-300/20 hover:bg-red-500/10" : ""}`}
                      >
                        <div
                          className={`flex h-10 w-10 items-center justify-center rounded-xl ${
                            item.key === "logout"
                              ? "bg-red-500/10 text-red-300"
                              : isActive
                              ? "bg-white/10 text-cyan-300"
                              : "bg-white/[0.04] text-white/55 group-hover:text-cyan-300"
                          }`}
                        >
                          <Icon className="h-5 w-5" />
                        </div>

                        <span
                          className={`text-sm font-semibold ${
                            item.key === "logout"
                              ? "text-red-200"
                              : isActive
                              ? "text-white"
                              : "text-white/72"
                          }`}
                        >
                          {item.label}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          </aside>

          {menuOpen && (
            <button
              aria-label="Close menu"
              className="fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px] lg:hidden"
              onClick={() => setMenuOpen(false)}
            />
          )}

          <section className="relative min-w-0 flex-1 px-4 py-4 sm:px-6 lg:px-8 lg:py-7">
            <div className="mb-5 flex items-center justify-between rounded-3xl border border-white/10 bg-white/[0.05] px-4 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] backdrop-blur-xl sm:px-5">
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setMenuOpen(true)}
                  className="flex h-11 w-11 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.05] text-white/75 lg:hidden"
                  aria-label="Open menu"
                >
                  <Menu className="h-5 w-5" />
                </button>

                <div>
                  <p className="text-xs uppercase tracking-[0.22em] text-white/35">
                    NolimitzBots
                  </p>
                  <p className="text-sm font-semibold text-white/82">
                    Admin Dashboard
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <button className="hidden h-11 items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm font-medium text-white/70 sm:flex">
                  <Bell className="h-4 w-4 text-cyan-300" />
                  Alerts
                </button>

                <button
                  onClick={() => router.push("/admin/profile")}
                  className="group flex h-12 w-12 items-center justify-center overflow-hidden rounded-2xl border border-cyan-300/20 bg-gradient-to-br from-sky-400/25 to-blue-500/15 shadow-[0_0_18px_rgba(56,189,248,0.16)]"
                  title="Profile Settings"
                >
                  {adminProfile?.logo_url ? (
                    <img
                      src={
                        adminProfile.logo_url.startsWith("http")
                          ? adminProfile.logo_url
                          : `${baseUrl}${adminProfile.logo_url}`
                      }
                      alt="Admin Logo"
                      className="h-full w-full rounded-2xl object-cover"
                    />
                  ) : (
                    <div className="flex h-9 w-9 items-center justify-center rounded-full bg-white/80 text-slate-900 shadow-md">
                      <UserSquare2 className="h-5 w-5" />
                    </div>
                  )}
                </button>
              </div>
            </div>

            {activeTab === "dashboard" && (
              <>
                <div className="grid gap-5 xl:grid-cols-[1.2fr_0.8fr]">
                  <div className="rounded-[32px] border border-white/10 bg-white/[0.06] p-5 shadow-[0_25px_80px_rgba(2,8,23,0.40)] backdrop-blur-2xl sm:p-7">
                    <div className="max-w-3xl">
                      <div className="inline-flex items-center gap-2 rounded-full border border-emerald-300/20 bg-emerald-400/10 px-3 py-1 text-xs font-semibold text-emerald-300">
                        <span className="h-2 w-2 rounded-full bg-emerald-400" />
                        All systems operational
                      </div>

                      <h1 className="mt-4 text-3xl font-black tracking-tight text-white/92 sm:text-4xl">
                        Good evening,{" "}
                        <span className="bg-gradient-to-r from-cyan-300 via-sky-400 to-blue-500 bg-clip-text text-transparent">
                          {adminName}
                        </span>
                      </h1>

                      <p className="mt-3 max-w-2xl text-sm leading-7 text-white/62 sm:text-base">
                        Welcome back to your NolimitzBots control center. Your
                        licenses, users, Expert Advisors, and platform tools are
                        ready.
                      </p>

                      <div className="mt-5 flex flex-wrap items-center gap-3">
                        <div className="inline-flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.05] px-4 py-3 text-sm font-medium text-white/78">
                          <UserSquare2 className="h-4 w-4 text-cyan-300" />
                          Admin ID: {adminId}
                        </div>

                        <div className="inline-flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.05] px-4 py-3 text-sm font-medium text-white/72">
                          <CalendarDays className="h-4 w-4 text-sky-300" />
                          {today}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-[32px] border border-white/10 bg-white/[0.06] p-5 shadow-[0_25px_80px_rgba(2,8,23,0.40)] backdrop-blur-2xl sm:p-7">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <p className="text-xs uppercase tracking-[0.24em] text-white/35">
                          Status Summary
                        </p>
                        <h2 className="mt-3 text-2xl font-bold text-white/90">
                          Your system is ready
                        </h2>
                      </div>

                      <div className="rounded-2xl border border-white/10 bg-white/[0.05] p-3">
                        <Shield className="h-5 w-5 text-cyan-300" />
                      </div>
                    </div>

                    <div className="mt-6 space-y-3">
                      {[
                        {
                          title: "Licensing engine online",
                          desc: "Key generation and activation are healthy.",
                        },
                        {
                          title: "EA inventory synced",
                          desc: "Your Expert Advisors are available for management.",
                        },
                        {
                          title: "User management ready",
                          desc: "Review subscriptions, licenses, and client records.",
                        },
                      ].map((status) => (
                        <div
                          key={status.title}
                          className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4"
                        >
                          <p className="text-sm font-semibold text-white/86">
                            {status.title}
                          </p>
                          <p className="mt-1 text-sm text-white/52">
                            {status.desc}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="mt-5 grid gap-5 md:grid-cols-2 xl:grid-cols-3">
                  {[
                    {
                      title: "Active Subscriptions",
                      value: stats.active_subscriptions,
                      note: "Currently active EA users",
                      glow: "from-emerald-400/25 via-cyan-400/10 to-transparent",
                      border: "border-emerald-300/20",
                      dot: "bg-emerald-400",
                    },
                    {
                      title: "Total License Key Users",
                      value: stats.total_license_key_users,
                      note: "All generated key holders",
                      glow: "from-sky-400/25 via-blue-400/10 to-transparent",
                      border: "border-sky-300/20",
                      dot: "bg-sky-400",
                    },
                    {
                      title: "Total EAs",
                      value: stats.total_eas,
                      note: "Expert Advisors in your inventory",
                      glow: "from-violet-400/25 via-fuchsia-400/10 to-transparent",
                      border: "border-violet-300/20",
                      dot: "bg-violet-400",
                    },
                  ].map((item) => (
                    <div
                      key={item.title}
                      className={`relative overflow-hidden rounded-[30px] border ${item.border} bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.30)] backdrop-blur-2xl`}
                    >
                      <div
                        className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${item.glow}`}
                      />

                      <div className="relative">
                        <div className="flex items-center justify-between">
                          <p className="text-sm font-semibold uppercase tracking-[0.18em] text-white/42">
                            {item.title}
                          </p>
                          <span className={`h-3 w-3 rounded-full ${item.dot}`} />
                        </div>

                        <p
                          className="mt-6 text-5xl font-semibold tracking-tight text-white/86 sm:text-6xl"
                          style={{ fontVariantNumeric: "tabular-nums" }}
                        >
                          {loadingData ? "..." : item.value.toLocaleString()}
                        </p>

                        <p className="mt-3 text-sm text-white/52">{item.note}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}

            {activeTab === "generate" && (
  <div className="mt-1 space-y-5">
    <div className="rounded-[32px] border border-white/10 bg-white/[0.06] p-6 backdrop-blur-2xl">
      <p className="text-xs uppercase tracking-[0.24em] text-white/35">
        Generate License
      </p>
      <h2 className="mt-3 text-3xl font-black text-white/92">
        Create License Key
      </h2>
      <p className="mt-3 text-sm text-white/56">
        Generate a license key for your client and assign it to an EA.
      </p>
    </div>

    <div className="rounded-[30px] border border-white/10 bg-white/[0.06] p-6 backdrop-blur-2xl">
      <div className="grid gap-4 md:grid-cols-2">

        {/* EA */}
        <div>
          <label className="mb-2 block text-xs text-white/40">
            Expert Advisor
          </label>
          <select
            value={licenseEA}
            onChange={(e) => setLicenseEA(e.target.value)}
            className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-white"
          >
            <option value="">Select EA</option>
            {eas.map((ea) => (
              <option key={ea.id} value={ea.id}>
                {ea.ea_name || ea.name}
              </option>
            ))}
          </select>
        </div>

        {/* Duration */}
        <div>
          <label className="mb-2 block text-xs text-white/40">
            Duration
          </label>
          <select
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
            className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-white"
          >
            <option value="30days">30 Days</option>
            <option value="1month">1 Month</option>
            <option value="1year">1 Year</option>
            <option value="lifetime">Lifetime</option>
          </select>
        </div>

        {/* Name */}
        <div>
          <label className="mb-2 block text-xs text-white/40">
            Client Name
          </label>
          <input
            value={clientName}
            onChange={(e) => setClientName(e.target.value)}
            placeholder="John Doe"
            className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-white"
          />
        </div>

        {/* Email */}
        <div>
          <label className="mb-2 block text-xs text-white/40">
            Email
          </label>
          <input
            value={clientEmail}
            onChange={(e) => setClientEmail(e.target.value)}
            placeholder="john@email.com"
            className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-white"
          />
        </div>
      </div>

      {licenseMessage && (
        <div className="mt-4 rounded-2xl border border-cyan-300/20 bg-cyan-400/10 px-4 py-3 text-sm text-cyan-200">
          {licenseMessage}
        </div>
      )}

      <div className="mt-5">
        <button
          onClick={handleCreateLicense}
          disabled={creatingLicense}
          className="rounded-2xl border border-cyan-300/20 bg-cyan-400/10 px-5 py-3 text-sm font-semibold text-cyan-300"
        >
          {creatingLicense ? "Generating..." : "Generate License"}
        </button>
      </div>

      {generatedLicense && (
  <div className="mt-6 rounded-[28px] border border-white/10 bg-white/[0.05] p-5 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl">
    <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4 text-[15px] leading-7 text-white/80">
      This licence key grants access to{" "}
      <span className="font-semibold text-cyan-300">{selectedEAName}</span>.
      Share it with the user to authorise their account.
    </div>

    <div className="mt-5 rounded-2xl border border-cyan-300/10 bg-[#07111f] p-5">
      <p className="text-xs uppercase tracking-[0.18em] text-white/35">
        License Key
      </p>

      <p className="mt-3 break-all font-mono text-lg text-cyan-300">
        {revealLicenseKey ? generatedLicense.license_key : "••••••••••••••••"}
      </p>

      <div className="mt-4 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={() => setRevealLicenseKey((prev) => !prev)}
          className="rounded-2xl border border-white/10 bg-white/[0.05] px-4 py-2 text-xs font-semibold text-white/75 transition hover:bg-white/[0.08]"
        >
          {revealLicenseKey ? "Hide Key" : "Reveal Key"}
        </button>

        <button
          type="button"
          onClick={handleCopyGeneratedLicense}
          className="rounded-2xl border border-cyan-300/20 bg-cyan-400/10 px-4 py-2 text-xs font-semibold text-cyan-300 transition hover:bg-cyan-400/15"
        >
          {copiedLicenseKey ? "Copied" : "Copy Key"}
        </button>
      </div>
    </div>

    <div className="mt-5 overflow-hidden rounded-2xl border border-white/10 bg-white/[0.04]">
      <div className="border-b border-white/10 px-4 py-3 text-xs font-semibold uppercase tracking-[0.18em] text-white/35">
        Key Information
      </div>

      <div className="divide-y divide-white/10">
        <div className="px-4 py-4">
          <p className="text-xs uppercase tracking-[0.16em] text-white/35">User</p>
          <p className="mt-2 text-lg font-semibold text-emerald-300">
            {generatedLicense.client_name}
          </p>
        </div>

        <div className="px-4 py-4">
          <p className="text-xs uppercase tracking-[0.16em] text-white/35">Expert Advisor</p>
          <p className="mt-2 text-lg font-semibold text-white/90">
            {selectedEAName}
          </p>
        </div>

        <div className="px-4 py-4">
          <p className="text-xs uppercase tracking-[0.16em] text-white/35">Plan</p>
          <p className="mt-2 text-lg font-semibold text-white/90">
  {generatedLicense.duration === "30days"
    ? "30 Days"
    : generatedLicense.duration === "1month"
    ? "1 Month"
    : generatedLicense.duration === "1year"
    ? "1 Year"
    : generatedLicense.duration === "lifetime"
    ? "Lifetime"
    : selectedDurationLabel}
</p>
        </div>

        <div className="px-4 py-4">
          <p className="text-xs uppercase tracking-[0.16em] text-white/35">Expires</p>
          <p className="mt-2 text-lg font-semibold text-white/90">
            {formatDateValue(generatedLicense.expires_at)}
          </p>
        </div>
      </div>
    </div>
  </div>
)}
    </div>
  </div>
)}

            {activeTab === "stats" && (
              <div className="mt-1 space-y-5">
                <div className="rounded-[32px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.30)] backdrop-blur-2xl sm:p-7">
                  <p className="text-xs uppercase tracking-[0.24em] text-white/35">
                    Key Stats
                  </p>
                  <h2 className="mt-3 text-3xl font-black tracking-tight text-white/92">
                    License & Platform Overview
                  </h2>
                  <p className="mt-3 max-w-2xl text-sm leading-7 text-white/56">
                    Monitor your licenses, users, and Expert Advisors in one place.
                  </p>
                </div>

                <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
                  {[
                    {
                      title: "Total Licenses",
                      value: licenses.length,
                      note: "All generated license keys",
                      dot: "bg-cyan-400",
                      border: "border-cyan-300/20",
                    },
                    {
                      title: "Active Licenses",
                      value: licenses.filter((item) => item.is_active ?? item.active).length,
                      note: "Currently usable keys",
                      dot: "bg-emerald-400",
                      border: "border-emerald-300/20",
                    },
                    {
                      title: "Inactive Licenses",
                      value: licenses.filter((item) => !(item.is_active ?? item.active)).length,
                      note: "Disabled or expired keys",
                      dot: "bg-red-400",
                      border: "border-red-300/20",
                    },
                    {
                      title: "Total EAs",
                      value: stats.total_eas,
                      note: "Expert Advisors in system",
                      dot: "bg-violet-400",
                      border: "border-violet-300/20",
                    },
                  ].map((item) => (
                    <div
                      key={item.title}
                      className={`rounded-[28px] border ${item.border} bg-white/[0.06] p-5 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl`}
                    >
                      <div className="flex items-center justify-between">
                        <p className="text-xs uppercase tracking-[0.18em] text-white/40">
                          {item.title}
                        </p>
                        <span className={`h-3 w-3 rounded-full ${item.dot}`} />
                      </div>

                      <p
                        className="mt-5 text-4xl font-black tracking-tight text-white/90"
                        style={{ fontVariantNumeric: "tabular-nums" }}
                      >
                        {loadingData ? "..." : item.value.toLocaleString()}
                      </p>

                      <p className="mt-2 text-sm text-white/52">{item.note}</p>
                    </div>
                  ))}
                </div>

                <div className="grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
                  <div className="rounded-[30px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl">
                    <p className="text-xs uppercase tracking-[0.22em] text-white/35">
                      Users Summary
                    </p>

                    <div className="mt-5 space-y-4">
                      <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
                        <p className="text-sm text-white/48">Unique Users</p>
                        <p className="mt-1 text-2xl font-bold text-white/90">
                          {
                            new Set(
                              licenses.map((item) =>
                                safeText(
                                  item.client_email || item.email || item.client_name || "unknown",
                                  ""
                                ).toLowerCase()
                              )
                            ).size
                          }
                        </p>
                      </div>

                      <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
                        <p className="text-sm text-white/48">License Key Users</p>
                        <p className="mt-1 text-2xl font-bold text-white/90">
                          {stats.total_license_key_users}
                        </p>
                      </div>

                      <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
                        <p className="text-sm text-white/48">Subscriptions</p>
                        <p className="mt-1 text-2xl font-bold text-white/90">
                          {stats.active_subscriptions}
                        </p>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-[30px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-xs uppercase tracking-[0.22em] text-white/35">
                          Recent License Activity
                        </p>
                        <p className="mt-2 text-sm text-white/52">
                          Latest generated license keys in your system.
                        </p>
                      </div>
                    </div>

                    <div className="mt-5 space-y-3">
                      {licenses.length === 0 ? (
                        <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-6 text-sm text-white/42">
                          No license activity yet.
                        </div>
                      ) : (
                        licenses.slice(0, 6).map((item, index) => (
                          <div
                            key={String(item.id ?? item.license_id ?? index)}
                            className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4"
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <p className="truncate text-sm font-semibold text-white/88">
                                  {safeText(item.client_name, "Unknown User")}
                                </p>
                                <p className="truncate text-sm text-white/48">
                                  {safeText(item.client_email || item.email, "No email")}
                                </p>
                              </div>

                              <span
                                className={`rounded-full px-3 py-1 text-xs font-semibold ${
                                  item.is_active ?? item.active
                                    ? "border border-emerald-300/20 bg-emerald-400/10 text-emerald-300"
                                    : "border border-red-300/20 bg-red-400/10 text-red-300"
                                }`}
                              >
                                {item.is_active ?? item.active ? "Active" : "Inactive"}
                              </span>
                            </div>

                            <div className="mt-3 flex flex-wrap gap-2">
                              <span className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-xs text-white/68">
                                {safeText(item.ea_name || item.robot_name, "EA")}
                              </span>
                              <span className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-xs text-white/68">
                                {safeText(item.duration || item.duration_days, "Plan")}
                              </span>
                            </div>

                            <p className="mt-3 truncate text-xs font-mono text-cyan-300">
                              {safeText(item.license_key || item.key, "No key")}
                            </p>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {activeTab === "licenses" && (
              <div className="mt-1 space-y-5">
                <div className="rounded-[32px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.30)] backdrop-blur-2xl sm:p-7">
                  <p className="text-xs uppercase tracking-[0.24em] text-white/35">
                    Licenses
                  </p>
                  <h2 className="mt-3 text-3xl font-black tracking-tight text-white/92">
                    License Manager
                  </h2>
                  <p className="mt-3 max-w-2xl text-sm leading-7 text-white/56">
                    Search, review, and manage all generated license keys for your users.
                  </p>
                </div>

                <div className="grid gap-5 xl:grid-cols-[0.8fr_1.2fr]">
                  <div className="rounded-[30px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl">
                    <p className="text-xs uppercase tracking-[0.22em] text-white/35">
                      Search Licenses
                    </p>

                    <div className="mt-4">
                      <input
                        value={licenseSearch}
                        onChange={(e) => setLicenseSearch(e.target.value)}
                        placeholder="Search by name, email, EA, key..."
                        className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none placeholder:text-white/30 focus:border-cyan-300/25 focus:bg-white/[0.07]"
                      />
                    </div>

                    <div className="mt-5 space-y-3">
                      <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
                        <p className="text-sm text-white/48">Total Results</p>
                        <p className="mt-1 text-2xl font-bold text-white/90">
                          {filteredLicenses.length}
                        </p>
                      </div>

                      <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
                        <p className="text-sm text-white/48">Active</p>
                        <p className="mt-1 text-2xl font-bold text-emerald-300">
                          {filteredLicenses.filter((item) => item.is_active ?? item.active).length}
                        </p>
                      </div>

                      <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
                        <p className="text-sm text-white/48">Inactive</p>
                        <p className="mt-1 text-2xl font-bold text-red-300">
                          {filteredLicenses.filter((item) => !(item.is_active ?? item.active)).length}
                        </p>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-[30px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-xs uppercase tracking-[0.22em] text-white/35">
                          All License Keys
                        </p>
                        <p className="mt-2 text-sm text-white/52">
                          Generated license records in your system.
                        </p>
                      </div>
                    </div>

                    <div className="mt-5 max-h-[650px] space-y-3 overflow-y-auto pr-1">
                      {filteredLicenses.length === 0 ? (
                        <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-6 text-sm text-white/42">
                          No licenses found.
                        </div>
                      ) : (
                        filteredLicenses.map((item, index) => {
                          const currentId = item.id ?? item.license_id ?? index;

                          return (
                            <div
                              key={String(currentId)}
                              className="rounded-[24px] border border-white/10 bg-white/[0.04] px-4 py-4"
                            >
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                  <p className="truncate text-sm font-semibold text-white/88">
                                    {safeText(item.client_name, "Unknown User")}
                                  </p>
                                  <p className="truncate text-sm text-white/48">
                                    {safeText(item.client_email || item.email, "No email")}
                                  </p>
                                </div>

                                <span
                                  className={`rounded-full px-3 py-1 text-xs font-semibold ${
                                    item.is_active ?? item.active
                                      ? "border border-emerald-300/20 bg-emerald-400/10 text-emerald-300"
                                      : "border border-red-300/20 bg-red-400/10 text-red-300"
                                  }`}
                                >
                                  {item.is_active ?? item.active ? "Active" : "Inactive"}
                                </span>
                              </div>

                              <div className="mt-3 flex flex-wrap gap-2">
                                <span className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-xs text-white/68">
                                  {safeText(item.ea_name || item.robot_name, "EA")}
                                </span>

                                <span className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-xs text-white/68">
                                  {safeText(item.duration || item.duration_days, "Plan")}
                                </span>
                              </div>

                              <div className="mt-3 rounded-2xl border border-cyan-300/10 bg-[#07111f] px-4 py-3">
                                <p className="truncate text-xs font-mono text-cyan-300">
                                  {safeText(item.license_key || item.key, "No key")}
                                </p>
                              </div>

                              <div className="mt-4 flex flex-wrap gap-3">
                                <button
                                  type="button"
                                  onClick={async () => {
                                    const keyToCopy = safeText(item.license_key || item.key, "").trim();
                                    if (!keyToCopy) return;

                                    try {
                                      await navigator.clipboard.writeText(keyToCopy);
                                      setCopiedId(currentId);

                                      setTimeout(() => {
                                        setCopiedId(null);
                                      }, 1500);
                                    } catch (err) {
                                      console.warn("Copy failed", err);
                                    }
                                  }}
                                  className="rounded-2xl border border-cyan-300/20 bg-cyan-400/10 px-4 py-2 text-xs font-semibold text-cyan-300 transition hover:bg-cyan-400/15"
                                >
                                  {copiedId === currentId ? "Copied" : "Copy Key"}
                                </button>

                                {item.is_active ?? item.active ? (
                                  <button
                                    type="button"
                                    onClick={() =>
                                      handleDeactivateLicense(item.id ?? item.license_id)
                                    }
                                    className="rounded-2xl border border-red-300/20 bg-red-400/10 px-4 py-2 text-xs font-semibold text-red-300 transition hover:bg-red-400/15"
                                  >
                                    Deactivate
                                  </button>
                                ) : (
                                  <button
                                    type="button"
                                    disabled
                                    className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-2 text-xs font-semibold text-white/40"
                                  >
                                    Inactive
                                  </button>
                                )}
                              </div>
                            </div>
                          );
                        })
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {activeTab === "users" && (
              <div className="mt-1 space-y-5">
                <div className="rounded-[32px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.30)] backdrop-blur-2xl sm:p-7">
                  <p className="text-xs uppercase tracking-[0.24em] text-white/35">
                    My Users
                  </p>
                  <h2 className="mt-3 text-3xl font-black tracking-tight text-white/92">
                    User Manager
                  </h2>
                  <p className="mt-3 max-w-2xl text-sm leading-7 text-white/56">
                    View all users connected to your license keys and see how many active and inactive licenses each user has.
                  </p>
                </div>

                <div className="grid gap-5 xl:grid-cols-[0.8fr_1.2fr]">
                  <div className="rounded-[30px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl">
                    <p className="text-xs uppercase tracking-[0.22em] text-white/35">
                      Search Users
                    </p>

                    <div className="mt-4">
                      <input
                        value={userSearch}
                        onChange={(e) => setUserSearch(e.target.value)}
                        placeholder="Search by user, email, or EA..."
                        className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none placeholder:text-white/30 focus:border-cyan-300/25 focus:bg-white/[0.07]"
                      />
                    </div>

                    <div className="mt-5 space-y-3">
                      <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
                        <p className="text-sm text-white/48">Total Users</p>
                        <p className="mt-1 text-2xl font-bold text-white/90">
                          {filteredUsers.length}
                        </p>
                      </div>

                      <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
                        <p className="text-sm text-white/48">Users With Active Keys</p>
                        <p className="mt-1 text-2xl font-bold text-emerald-300">
                          {filteredUsers.filter((user) => user.activeCount > 0).length}
                        </p>
                      </div>

                      <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
                        <p className="text-sm text-white/48">Users With Only Inactive Keys</p>
                        <p className="mt-1 text-2xl font-bold text-red-300">
                          {filteredUsers.filter((user) => user.activeCount === 0).length}
                        </p>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-[30px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl">
                    <div>
                      <p className="text-xs uppercase tracking-[0.22em] text-white/35">
                        User List
                      </p>
                      <p className="mt-2 text-sm text-white/52">
                        All users grouped by email or client name.
                      </p>
                    </div>

                    <div className="mt-5 max-h-[650px] space-y-3 overflow-y-auto pr-1">
                      {filteredUsers.length === 0 ? (
                        <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-6 text-sm text-white/42">
                          No users found.
                        </div>
                      ) : (
                        filteredUsers.map((user, index) => (
                          <div
                            key={`${user.email}-${index}`}
                            className="rounded-[24px] border border-white/10 bg-white/[0.04] px-4 py-4"
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <p className="truncate text-sm font-semibold text-white/88">
                                  {safeText(user.name, "Unknown User")}
                                </p>
                                <p className="truncate text-sm text-white/48">
                                  {safeText(user.email, "No email")}
                                </p>
                              </div>

                              <span className="rounded-full border border-cyan-300/20 bg-cyan-400/10 px-3 py-1 text-xs font-semibold text-cyan-300">
                                {user.licenses.length} Key{user.licenses.length !== 1 ? "s" : ""}
                              </span>
                            </div>

                            <div className="mt-3 flex flex-wrap gap-2">
                              <span className="rounded-full border border-emerald-300/20 bg-emerald-400/10 px-3 py-1 text-xs text-emerald-300">
                                Active: {user.activeCount}
                              </span>
                              <span className="rounded-full border border-red-300/20 bg-red-400/10 px-3 py-1 text-xs text-red-300">
                                Inactive: {user.inactiveCount}
                              </span>
                            </div>

                            <div className="mt-3 flex flex-wrap gap-2">
                              {user.eas.length > 0 ? (
                                user.eas.map((ea) => (
                                  <span
                                    key={ea}
                                    className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-xs text-white/68"
                                  >
                                    {safeText(ea, "EA")}
                                  </span>
                                ))
                              ) : (
                                <span className="text-xs text-white/38">No EA assigned</span>
                              )}
                            </div>

                            <div className="mt-4 rounded-2xl border border-white/10 bg-[#07111f] px-4 py-3">
                              <p className="text-xs text-white/45">Latest Key</p>
                              <p className="mt-1 truncate font-mono text-xs text-cyan-300">
                                {safeText(
                                  user.licenses[0]?.license_key || user.licenses[0]?.key,
                                  "No key"
                                )}
                              </p>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {activeTab === "push-signals" && (
              <div className="mt-1 space-y-5">
                <div className="rounded-[32px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.30)] backdrop-blur-2xl sm:p-7">
                  <p className="text-xs uppercase tracking-[0.24em] text-white/35">
                    Push Signals
                  </p>
                  <h2 className="mt-3 text-3xl font-black tracking-tight text-white/92">
                    Send Manual Signal
                  </h2>
                  <p className="mt-3 max-w-2xl text-sm leading-7 text-white/56">
                    Broadcast a trading signal to users connected to the selected EA.
                  </p>
                </div>

                <div className="rounded-[30px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl">
                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-white/40">
                        Expert Advisor
                      </label>
                      <select
                        value={signalEA}
                        onChange={(e) => setSignalEA(e.target.value)}
                        className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none"
                      >
                        <option value="">Select EA</option>
                        {eas.map((ea) => (
                          <option key={String(ea.id)} value={String(ea.id ?? "")}>
                            {safeText(ea.ea_name || ea.name, `EA ${ea.id}`)}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div>
                      <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-white/40">
                        Symbol
                      </label>
                      <input
                        value={signalSymbol}
                        onChange={(e) => setSignalSymbol(e.target.value)}
                        placeholder="XAUUSD"
                        className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none placeholder:text-white/30"
                      />
                    </div>

                    <div>
                      <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-white/40">
                        Action
                      </label>
                      <select
                        value={signalAction}
                        onChange={(e) => setSignalAction(e.target.value)}
                        className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none"
                      >
                        <option value="buy">Buy</option>
                        <option value="sell">Sell</option>
                      </select>
                    </div>

                    <div>
                      <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-white/40">
                        Stop Loss
                      </label>
                      <input
                        value={signalSL}
                        onChange={(e) => setSignalSL(e.target.value)}
                        placeholder="Optional SL"
                        className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none placeholder:text-white/30"
                      />
                    </div>

                    <div>
                      <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-white/40">
                        Take Profit
                      </label>
                      <input
                        value={signalTP}
                        onChange={(e) => setSignalTP(e.target.value)}
                        placeholder="Optional TP"
                        className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none placeholder:text-white/30"
                      />
                    </div>

                    <div>
                      <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-white/40">
                        Comment
                      </label>
                      <input
                        value={signalComment}
                        onChange={(e) => setSignalComment(e.target.value)}
                        placeholder="Optional note"
                        className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none placeholder:text-white/30"
                      />
                    </div>
                  </div>

                  {signalMessage && (
                    <div className="mt-4 rounded-2xl border border-cyan-300/20 bg-cyan-400/10 px-4 py-3 text-sm text-cyan-200">
                      {signalMessage}
                    </div>
                  )}

                  <div className="mt-5">
                    <button
                      type="button"
                      onClick={handlePushSignal}
                      disabled={sendingSignal}
                      className="rounded-2xl border border-cyan-300/20 bg-cyan-400/10 px-5 py-3 text-sm font-semibold text-cyan-300 transition hover:bg-cyan-400/15 disabled:opacity-60"
                    >
                      {sendingSignal ? "Sending Signal..." : "Push Signal"}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {activeTab === "control-bot" && (
  <div className="mt-1 space-y-5">
    <div className="rounded-[32px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.30)] backdrop-blur-2xl sm:p-7">
      <p className="text-xs uppercase tracking-[0.24em] text-white/35">
        Control Bot
      </p>
      <h2 className="mt-3 text-3xl font-black tracking-tight text-white/92">
        Connect Trading Account
      </h2>
      <p className="mt-3 max-w-3xl text-sm leading-7 text-white/56">
        Link your MT5 trading account to the selected EA. When Control Bot is connected,
        trades opened on this account can automatically be copied to users linked to that EA.
      </p>
    </div>

    <div className="grid gap-5 xl:grid-cols-[1fr_0.9fr]">
      <div className="rounded-[30px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl">
        <p className="text-xs uppercase tracking-[0.22em] text-white/35">
          Trading Account Details
        </p>

        <div className="mt-5 grid gap-4">
          <div>
            <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-white/40">
              Expert Advisor
            </label>
            <select
              value={masterEA}
              onChange={(e) => setMasterEA(e.target.value)}
              className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none"
            >
              <option value="">Select EA</option>
              {eas.map((ea) => (
                <option key={ea.id} value={ea.id}>
                  {ea.ea_name || ea.name || `EA ${ea.id}`}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-white/40">
              MT5 Login
            </label>
            <input
              value={masterLogin}
              onChange={(e) => setMasterLogin(e.target.value)}
              placeholder="Enter MT5 login"
              className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none placeholder:text-white/30"
            />
          </div>

          <div>
            <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-white/40">
              MT5 Password
            </label>
            <input
              type="password"
              value={masterPassword}
              onChange={(e) => setMasterPassword(e.target.value)}
              placeholder="Enter MT5 password"
              className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none placeholder:text-white/30"
            />
          </div>

          <div>
            <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-white/40">
              MT5 Server
            </label>
            <input
              value={masterServer}
              onChange={(e) => setMasterServer(e.target.value)}
              placeholder="Example: Exness-MT5Real6"
              className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none placeholder:text-white/30"
            />
          </div>
        </div>

        {masterMessage && (
  <div
    className={`mt-4 rounded-2xl border px-4 py-3 text-sm ${
      masterStatus === "Connected"
        ? "border-emerald-300/20 bg-emerald-400/10 text-emerald-200"
        : masterStatus === "Connection failed"
        ? "border-red-300/20 bg-red-400/10 text-red-200"
        : "border-cyan-300/20 bg-cyan-400/10 text-cyan-200"
    }`}
  >
    {masterMessage}
  </div>
)}

        <div className="mt-5 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={handleSaveMasterAccount}
            disabled={savingMaster}
            className="rounded-2xl border border-cyan-300/20 bg-cyan-400/10 px-5 py-3 text-sm font-semibold text-cyan-300 transition hover:bg-cyan-400/15 disabled:opacity-60"
          >
            {savingMaster ? "Saving..." : "Save Account"}
          </button>

          <button
            type="button"
            onClick={handleVerifyMasterAccount}
            disabled={verifyingMaster}
            className="rounded-2xl border border-emerald-300/20 bg-emerald-400/10 px-5 py-3 text-sm font-semibold text-emerald-300 transition hover:bg-emerald-400/15 disabled:opacity-60"
          >
            {verifyingMaster ? "Verifying..." : "Verify Connection"}
          </button>
        </div>
      </div>

      <div className="rounded-[30px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl">
        <p className="text-xs uppercase tracking-[0.22em] text-white/35">
          Bot Status
        </p>

        <div className="mt-5 space-y-4">
          <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
            <p className="text-xs text-white/40">Status</p>
            <p className="mt-1 text-sm font-semibold text-white/90">
              {masterStatus}
            </p>
          </div>

          <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
  <p className="text-xs text-white/40">Connection Status</p>
  <div className="mt-2">
    <span
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold ${
        masterStatus === "Connected"
          ? "border border-emerald-300/20 bg-emerald-400/10 text-emerald-300"
          : masterStatus === "Saved"
          ? "border border-cyan-300/20 bg-cyan-400/10 text-cyan-300"
          : masterStatus === "Connection failed"
          ? "border border-red-300/20 bg-red-400/10 text-red-300"
          : "border border-white/10 bg-white/[0.05] text-white/60"
      }`}
    >
      <span
        className={`h-2 w-2 rounded-full ${
          masterStatus === "Connected"
            ? "bg-emerald-400"
            : masterStatus === "Saved"
            ? "bg-cyan-400"
            : masterStatus === "Connection failed"
            ? "bg-red-400"
            : "bg-white/30"
        }`}
      />
      {masterStatus}
    </span>
  </div>
</div>

<div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
  <p className="text-xs text-white/40">Account Name</p>
  <p className="mt-1 text-sm font-semibold text-white/90">
    {masterAccountName || "Not verified yet"}
  </p>
</div>

          <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
            <p className="text-xs text-white/40">Broker</p>
            <p className="mt-1 text-sm font-semibold text-white/90">
              {masterBrokerName || "Not verified yet"}
            </p>
          </div>

          <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
            <p className="text-xs text-white/40">Linked EA</p>
            <p className="mt-1 text-sm font-semibold text-white/90">
  {eas.find((ea) => String(ea.id) === String(masterEA))?.ea_name ||
    eas.find((ea) => String(ea.id) === String(masterEA))?.name ||
    "Not selected"}
</p>
          </div>

          <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-4 text-sm leading-6 text-white/50">
            When Control Bot is connected, trades opened on this MT5 account can be
            mirrored to clients linked to the selected EA automatically.
          </div>
        </div>
      </div>
    </div>
  </div>
)}

            {activeTab === "reactivate" && (
              <div className="mt-1 space-y-5">
                <div className="rounded-[32px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.30)] backdrop-blur-2xl sm:p-7">
                  <p className="text-xs uppercase tracking-[0.24em] text-white/35">
                    Re-Activate Key
                  </p>
                  <h2 className="mt-3 text-3xl font-black tracking-tight text-white/92">
                    Reset Device Lock
                  </h2>
                  <p className="mt-3 max-w-2xl text-sm leading-7 text-white/56">
                    Search a license key and reset its device lock so the client can connect again.
                  </p>
                </div>

                <div className="grid gap-5 xl:grid-cols-[0.8fr_1.2fr]">
                  <div className="rounded-[30px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl">
                    <p className="text-xs uppercase tracking-[0.22em] text-white/35">
                      Search License Key
                    </p>

                    <div className="mt-4 space-y-3">
                      <input
                        value={reactivateSearch}
                        onChange={(e) => setReactivateSearch(e.target.value)}
                        placeholder="Enter exact license key..."
                        className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm text-white outline-none placeholder:text-white/30 focus:border-cyan-300/25 focus:bg-white/[0.07]"
                      />

                      <button
                        type="button"
                        onClick={handleSearchLicenseForReactivation}
                        disabled={reactivateLoading || !reactivateSearch.trim()}
                        className="inline-flex h-11 items-center justify-center rounded-2xl border border-cyan-300/20 bg-cyan-400/10 px-5 text-sm font-semibold text-cyan-300 transition hover:bg-cyan-400/15 disabled:opacity-50"
                      >
                        {reactivateLoading ? "Searching..." : "Search Key"}
                      </button>
                    </div>
                  </div>

                  <div className="rounded-[30px] border border-white/10 bg-white/[0.06] p-6 shadow-[0_20px_60px_rgba(2,8,23,0.28)] backdrop-blur-2xl">
                    <p className="text-xs uppercase tracking-[0.22em] text-white/35">
                      License Result
                    </p>

                    {!reactivateResult ? (
                      <div className="mt-5 rounded-2xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-6 text-sm text-white/42">
                        No license selected yet.
                      </div>
                    ) : (
                      <div className="mt-5 space-y-4">
                        <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
                          <p className="text-xs text-white/40">Client Name</p>
                          <p className="mt-1 text-sm font-semibold text-white/90">
                            {safeText(reactivateResult.client_name, "Unknown User")}
                          </p>
                        </div>

                        <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
                          <p className="text-xs text-white/40">Email</p>
                          <p className="mt-1 text-sm font-semibold text-white/90">
                            {safeText(
                              reactivateResult.client_email || reactivateResult.email,
                              "No email"
                            )}
                          </p>
                        </div>

                        <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
                          <p className="text-xs text-white/40">EA</p>
                          <p className="mt-1 text-sm font-semibold text-white/90">
                            {safeText(reactivateResult.ea_name || reactivateResult.robot_name, "EA")}
                          </p>
                        </div>

                        <div className="rounded-2xl border border-cyan-300/10 bg-[#07111f] px-4 py-4">
                          <p className="text-xs text-white/40">License Key</p>
                          <p className="mt-1 break-all font-mono text-sm text-cyan-300">
                            {safeText(reactivateResult.license_key || reactivateResult.key, "No key")}
                          </p>
                        </div>

                        <div className="flex flex-wrap gap-3">
                          <span
                            className={`rounded-full px-3 py-1 text-xs font-semibold ${
                              reactivateResult.is_active ?? reactivateResult.active
                                ? "border border-emerald-300/20 bg-emerald-400/10 text-emerald-300"
                                : "border border-red-300/20 bg-red-400/10 text-red-300"
                            }`}
                          >
                            {reactivateResult.is_active ?? reactivateResult.active
                              ? "Active"
                              : "Inactive"}
                          </span>
                        </div>

                        <button
                          type="button"
                          onClick={handleReactivateLicense}
                          disabled={reactivating || resetSuccess}
                          className="rounded-2xl border border-emerald-300/20 bg-emerald-400/10 px-5 py-2.5 text-sm font-semibold text-emerald-200"
                        >
                          {reactivating
                            ? "Resetting..."
                            : resetSuccess
                            ? "Reset Successful..."
                            : "Reset Device Lock"}
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            <div className="pb-8 pt-12 text-center text-sm text-white/28">
              Copyright © 2026 NolimitzBots. All rights reserved.
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
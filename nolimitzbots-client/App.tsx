import React, { useEffect, useState } from "react";
import EntryScreen from "./src/screens/EntryScreen";
import LicenseKeyScreen from "./src/screens/LicenseKeyScreen";
import MainTabs from "./src/navigation/MainTabs";
import * as SecureStore from "expo-secure-store";

type AppScreen = "entry" | "license" | "dashboard";
type ReturnTarget = "entry" | "dashboard";

type LicenseBranding = {
  licenseKey: string;
  robotName: string;
  activeUntil: string;
  ownerName: string;
  ownerContact: string;
  logoUrl: string;
  companyName: string;
};

const STORAGE_KEY = "nolimitz_user_session";

export default function App() {
  const [screen, setScreen] = useState<AppScreen>("entry");
  const [licenseReturnTarget, setLicenseReturnTarget] =
    useState<ReturnTarget>("entry");

  const [branding, setBranding] = useState<LicenseBranding>({
    licenseKey: "",
    robotName: "",
    activeUntil: "",
    ownerName: "",
    ownerContact: "",
    logoUrl: "",
    companyName: "",
  });

  const [loading, setLoading] = useState(true);

  // ✅ LOAD SAVED SESSION (AUTO LOGIN)
  useEffect(() => {
    const loadSession = async () => {
      try {
        const saved = await SecureStore.getItemAsync(STORAGE_KEY);

        if (saved) {
          const parsed = JSON.parse(saved);
          setBranding(parsed);
          setScreen("dashboard");
        }
      } catch (e) {
        console.log("Session load error:", e);
      } finally {
        setLoading(false);
      }
    };

    loadSession();
  }, []);

  // 🔥 OPEN LICENSE FROM ENTRY
  const openLicenseFromEntry = () => {
    setLicenseReturnTarget("entry");
    setScreen("license");
  };

  // 🔥 OPEN LICENSE FROM DASHBOARD
  const openLicenseFromDashboard = () => {
    setLicenseReturnTarget("dashboard");
    setScreen("license");
  };

  // ✅ AFTER VERIFY → SAVE SESSION
  const handleVerified = async (data: LicenseBranding) => {
    setBranding(data);

    try {
      await SecureStore.setItemAsync(STORAGE_KEY, JSON.stringify(data));
    } catch (e) {
      console.log("Session save error:", e);
    }

    setScreen("dashboard");
  };

  const handleBackFromLicense = () => {
    setScreen(licenseReturnTarget === "dashboard" ? "dashboard" : "entry");
  };

  // ❌ DELETE ROBOT → CLEAR SESSION
  const handleDeleteRobot = async () => {
    try {
      await SecureStore.deleteItemAsync(STORAGE_KEY);
    } catch (e) {
      console.log("Session delete error:", e);
    }

    setBranding({
      licenseKey: "",
      robotName: "",
      activeUntil: "",
      ownerName: "",
      ownerContact: "",
      logoUrl: "",
      companyName: "",
    });

    setScreen("entry");
  };

  // 🔄 LOADING SCREEN (important)
  if (loading) return null;

  if (screen === "entry") {
    return <EntryScreen onAddEA={openLicenseFromEntry} />;
  }

  if (screen === "license") {
    return (
      <LicenseKeyScreen
        onBack={handleBackFromLicense}
        onVerified={handleVerified}
      />
    );
  }

  return (
    <MainTabs
      licenseKey={branding.licenseKey}
      robotName={branding.robotName}
      activeUntil={branding.activeUntil}
      ownerName={branding.ownerName}
      ownerContact={branding.ownerContact}
      logoUrl={branding.logoUrl}
      companyName={branding.companyName}
      onAddRobot={openLicenseFromDashboard}
      onDeleteRobot={handleDeleteRobot}
    />
  );
}
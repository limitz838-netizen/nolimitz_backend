import React, { useState } from "react";
import {
  Alert,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import * as SecureStore from "expo-secure-store";
import * as Application from "expo-application";

import GlassCard from "../components/GlassCard";
import { Colors } from "../theme/colors";
import { Radius } from "../theme/radius";
import { Spacing } from "../theme/spacing";

const API_BASE =
  process.env.EXPO_PUBLIC_API_URL || "https://nolimitz-backend-yfne.onrender.com";

const DEVICE_ID_KEY = "nolimitzbots_device_id";

type VerifiedPayload = {
  licenseKey: string;
  robotName: string;
  activeUntil: string;
  ownerName: string;
  ownerContact: string;
  logoUrl: string;
  companyName: string;
};

type LicenseKeyScreenProps = {
  onBack: () => void;
  onVerified: (data: VerifiedPayload) => void;
};

async function getOrCreateDeviceId(): Promise<string> {
  const saved = await SecureStore.getItemAsync(DEVICE_ID_KEY);
  if (saved) return saved;

  const generated =
    Application.androidId ||
    `device-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;

  await SecureStore.setItemAsync(DEVICE_ID_KEY, generated);
  return generated;
}

export default function LicenseKeyScreen({
  onBack,
  onVerified,
}: LicenseKeyScreenProps) {
  const [licenseKey, setLicenseKey] = useState("");
  const [loading, setLoading] = useState(false);

  const handleVerify = async () => {
    const cleanKey = licenseKey.trim().toUpperCase();

    if (!cleanKey) {
      Alert.alert("Missing Key", "Please enter your license key.");
      return;
    }

    try {
      setLoading(true);

      const deviceId = await getOrCreateDeviceId();

      const response = await fetch(`${API_BASE}/client/activate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          license_key: cleanKey,
          device_id: deviceId,
          device_name: "Android Client",
        }),
      });

      const text = await response.text();

      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        data = {};
      }

      if (!response.ok) {
        Alert.alert(
          "Verification Failed",
          data?.detail || data?.message || "License verification failed."
        );
        return;
      }

      const branding = data?.branding || {};

      const robotName =
        data?.robot_name ||
        data?.ea_name ||
        data?.expert_name ||
        data?.ea_code_name ||
        data?.license?.robot_name ||
        data?.license?.ea_name ||
        "";

      const activeUntil =
        data?.active_until ||
        data?.expires_at ||
        data?.expiry_date ||
        data?.license?.active_until ||
        data?.license?.expires_at ||
        "";

      const ownerName =
        branding?.display_name ||
        data?.owner_name ||
        data?.admin_name ||
        data?.license?.owner_name ||
        "";

      const ownerContact =
        branding?.whatsapp ||
        branding?.telegram ||
        branding?.support_email ||
        data?.owner_contact ||
        data?.admin_contact ||
        data?.license?.owner_contact ||
        "";

      const companyName =
        data?.company_name ||
        data?.admin_company_name ||
        data?.brand_name ||
        data?.license?.company_name ||
        data?.license?.admin_company_name ||
        branding?.company_name ||
        branding?.display_name ||
        "";

      const rawLogo =
        data?.logo_url ||
        data?.admin_logo ||
        data?.logo ||
        data?.license?.logo_url ||
        data?.license?.admin_logo ||
        branding?.logo_url ||
        "";

      let logoUrl = "";

      if (rawLogo && typeof rawLogo === "string") {
        if (rawLogo.startsWith("http")) {
          logoUrl = rawLogo;
        } else if (rawLogo.startsWith("/")) {
          logoUrl = `${API_BASE}${rawLogo}`;
        } else {
          logoUrl = `${API_BASE}/${rawLogo}`;
        }
      }

      Alert.alert("Success", "License key verified successfully.", [
        {
          text: "OK",
          onPress: () =>
            onVerified({
              licenseKey: cleanKey,
              robotName,
              activeUntil,
              ownerName,
              ownerContact,
              logoUrl,
              companyName,
            }),
        },
      ]);
    } catch (error) {
      Alert.alert(
        "Connection Error",
        "Could not reach the server. Please check your internet connection and try again."
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <LinearGradient
      colors={["#07152D", "#0A2B63", "#0D56B5"]}
      style={styles.container}
    >
      <SafeAreaView style={styles.safe}>
        <View style={styles.content}>
          <Pressable style={styles.backButton} onPress={onBack}>
            <Ionicons name="arrow-back" size={18} color={Colors.text} />
          </Pressable>

          <GlassCard style={styles.card}>
            <Text style={styles.title}>ENTER LICENSE KEY</Text>
            <Text style={styles.subtitle}>
              Enter your valid license key to access the dashboard
            </Text>

            <Text style={styles.label}>License Key</Text>
            <TextInput
              value={licenseKey}
              onChangeText={setLicenseKey}
              placeholder="Enter your license key"
              placeholderTextColor="rgba(255,255,255,0.45)"
              style={styles.input}
              autoCapitalize="characters"
              autoCorrect={false}
              editable={!loading}
            />

            <Pressable
              style={[
                styles.verifyButton,
                loading && styles.verifyButtonDisabled,
              ]}
              onPress={handleVerify}
              disabled={loading}
            >
              <Ionicons
                name="shield-checkmark-outline"
                size={18}
                color="#08344C"
              />
              <Text style={styles.verifyButtonText}>
                {loading ? "Verifying..." : "Verify"}
              </Text>
            </Pressable>
          </GlassCard>
        </View>
      </SafeAreaView>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safe: { flex: 1 },
  content: {
    flex: 1,
    paddingHorizontal: Spacing.medium,
    paddingTop: 40,
  },
  backButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: "rgba(255,255,255,0.10)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.14)",
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 24,
  },
  card: {
    padding: 20,
    borderRadius: 28,
  },
  title: {
    color: Colors.text,
    fontSize: 22,
    fontWeight: "900",
    textAlign: "center",
    marginBottom: 10,
  },
  subtitle: {
    color: Colors.text,
    opacity: 0.85,
    fontSize: 14,
    textAlign: "center",
    lineHeight: 22,
    marginBottom: 22,
    fontWeight: "600",
  },
  label: {
    color: Colors.text,
    fontSize: 14,
    fontWeight: "700",
    marginBottom: 8,
  },
  input: {
    backgroundColor: "rgba(255,255,255,0.10)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.14)",
    borderRadius: Radius.medium,
    paddingHorizontal: 16,
    paddingVertical: 16,
    color: Colors.text,
    fontSize: 16,
    fontWeight: "700",
    marginBottom: 18,
  },
  verifyButton: {
    height: 56,
    borderRadius: 18,
    backgroundColor: "#16daf8",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  verifyButtonDisabled: {
    opacity: 0.7,
  },
  verifyButtonText: {
    color: "#08344C",
    fontSize: 17,
    fontWeight: "900",
  },
});
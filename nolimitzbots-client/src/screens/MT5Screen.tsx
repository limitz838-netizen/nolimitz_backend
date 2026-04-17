import React, { useEffect, useState, useCallback, useRef } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  Image,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

import GlassCard from "../components/GlassCard";
import { Colors } from "../theme/colors";
import { Spacing } from "../theme/spacing";

type MT5ScreenProps = {
  licenseKey: string;
  onMT5StatusChange?: (connected: boolean) => void;
};

type MT5StatusResponse = {
  license_key: string;
  mt_login: string | null;
  mt_server: string | null;
  is_active: boolean;
  verified: boolean;
  account_name: string | null;
  broker_name: string | null;
  balance: string | null;
  equity: string | null;
  last_verified_at: string | null;
  status: string;
  message: string;
};

const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_URL || "https://nolimitz-backend-yfne.onrender.com";

export default function MT5Screen({
  licenseKey,
  onMT5StatusChange,
}: MT5ScreenProps) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [server, setServer] = useState("");

  const [loading, setLoading] = useState(false);
  const [statusLoading, setStatusLoading] = useState(true);
  const [mt5Status, setMT5Status] = useState<MT5StatusResponse | null>(null);

  const pollTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const isMountedRef = useRef(true);

  const clearPoll = useCallback(() => {
    if (pollTimeoutRef.current) {
      clearTimeout(pollTimeoutRef.current);
      pollTimeoutRef.current = null;
    }
  }, []);

  const parseJsonSafely = async (response: Response): Promise<any> => {
    const text = await response.text();
    console.log("MT5 API Response:", text.substring(0, 400));

    if (!text?.trim()) return null;

    try {
      return JSON.parse(text);
    } catch (e) {
      console.error("JSON Parse Error:", e);
      throw new Error(text || "Invalid response from server");
    }
  };

  const getFriendlyMt5ErrorMessage = (raw?: string): string => {
    const text = String(raw || "").toLowerCase();

    if (!text) {
      return "Unable to connect your MetaTrader 5 account. Please try again.";
    }

    if (text.includes("invalid account") || text.includes("authorization failed") || text.includes("wrong") || text.includes("incorrect")) {
      return "Incorrect login, password, or server. Please double-check your credentials.";
    }

    if (text.includes("no connection") || text.includes("broker server") || text.includes("cannot connect")) {
      return "Could not connect to the broker server. Please verify the server name and your internet connection.";
    }

    if (text.includes("timeout") || text.includes("took too long")) {
      return "Connection timeout. Please check your internet and try again.";
    }

    if (text.includes("high reliability") || text.includes("agiliumtrade") || text.includes("temporarily unavailable")) {
      return "MetaTrader 5 connection is temporarily unavailable. Please try again in a few minutes.";
    }

    if (text.includes("expired") || text.includes("deactivated")) {
      return "Your license appears inactive or expired. Please contact support.";
    }

    return "Unable to connect your MT5 account. Please check your details or try again later.";
  };

  const fetchMT5Status = useCallback(
    async (showLoading = true) => {
      if (!licenseKey?.trim() || !isMountedRef.current) {
        setStatusLoading(false);
        return;
      }

      clearPoll();

      try {
        if (showLoading) setStatusLoading(true);

        const response = await fetch(`${API_BASE_URL}/client/mt5/status`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ license_key: licenseKey }),
        });

        const data = await parseJsonSafely(response);

        if (!response.ok) {
          const errorMsg = data?.message || data?.detail || "Failed to fetch status";
          throw new Error(errorMsg);
        }

        if (!data) throw new Error("Empty response");

        setMT5Status(data);

        // Auto-fill only if fields are empty (avoid overwriting user input)
        if (data.mt_login && !login) setLogin(data.mt_login);
        if (data.mt_server && !server) setServer(data.mt_server);

        const isConnected = data.status === "connected";
        onMT5StatusChange?.(isConnected);

        // Auto-poll during verification
        if ((data.status === "verifying" || data.status === "retry") && isMountedRef.current) {
          pollTimeoutRef.current = setTimeout(() => fetchMT5Status(false), 3000);
        }
      } catch (error: any) {
        console.error("MT5 status error:", error);
        setMT5Status(null);
        onMT5StatusChange?.(false);
      } finally {
        if (isMountedRef.current) setStatusLoading(false);
      }
    },
    [licenseKey, onMT5StatusChange, login, server, clearPoll]
  );

  const handleSaveMT5 = async () => {
    if (!login.trim() || !password.trim() || !server.trim()) {
      Alert.alert("Incomplete", "Please fill in Login, Password, and Server.");
      return;
    }

    clearPoll();

    try {
      setLoading(true);

      const response = await fetch(`${API_BASE_URL}/client/mt5/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          license_key: licenseKey,
          mt_login: login.trim(),
          mt_password: password.trim(),
          mt_server: server.trim(),
        }),
      });

      const data = await parseJsonSafely(response);

      if (!response.ok) {
        const rawError = data?.message || data?.detail || "Failed to save MT5 account";
        const friendlyError = getFriendlyMt5ErrorMessage(rawError);

        Alert.alert("Connection Failed", friendlyError);
        onMT5StatusChange?.(false);
        await fetchMT5Status(false);
        return;
      }

      Alert.alert("Success", data?.message || "MT5 credentials saved. Verification in progress...");

      setPassword(""); // Clear for security
      await fetchMT5Status(false);
    } catch (error: any) {
      console.error("MT5 save error:", error);
      const friendlyError = getFriendlyMt5ErrorMessage(error?.message);
      Alert.alert("Connection Error", friendlyError);
    } finally {
      setLoading(false);
    }
  };

  const handleRefresh = () => {
    fetchMT5Status(true);
  };

  // Derived status
  const status = mt5Status?.status ?? "not_connected";
  const isConnected = status === "connected";
  const isVerifying = status === "verifying" || status === "retry";
  const isFailed = status === "failed";

  const statusLabel = isConnected
    ? "Connected Successfully"
    : isVerifying
    ? "Verifying Connection..."
    : isFailed
    ? "Verification Failed"
    : "Not Connected";

  const statusColor = isConnected
    ? "#56EBB9"
    : isVerifying
    ? "#7FDBFF"
    : isFailed
    ? "#FF6B6B"
    : "#FFB4B4";

  const canEdit = !loading && !isVerifying;

  useEffect(() => {
    isMountedRef.current = true;
    fetchMT5Status();

    return () => {
      isMountedRef.current = false;
      clearPoll();
    };
  }, [fetchMT5Status, clearPoll]);

  return (
    <LinearGradient
      colors={["#050B18", "#09162E", "#0D2A57"]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.container}
    >
      <SafeAreaView style={styles.safe}>
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          {/* MT5 Form Card */}
          <GlassCard style={styles.formCard}>
            <View style={styles.headerRow}>
              <View style={styles.logoWrap}>
                <Image
                  source={{
                    uri: "https://upload.wikimedia.org/wikipedia/commons/f/fa/MetaTrader_5_Clear_240px.png",
                  }}
                  style={styles.logo}
                  resizeMode="contain"
                />
              </View>
              <Pressable onPress={handleRefresh} disabled={statusLoading || loading}>
                <Ionicons name="refresh-circle" size={28} color="#7FDBFF" />
              </Pressable>
            </View>

            <View style={styles.fieldWrap}>
              <Text style={styles.label}>MT5 Login</Text>
              <TextInput
                value={login}
                onChangeText={setLogin}
                placeholder="12345678"
                placeholderTextColor="rgba(255,255,255,0.4)"
                style={styles.input}
                keyboardType="number-pad"
                editable={canEdit}
                autoCapitalize="none"
              />
            </View>

            <View style={styles.fieldWrap}>
              <Text style={styles.label}>Password</Text>
              <TextInput
                value={password}
                onChangeText={setPassword}
                placeholder="Enter your MT5 password"
                placeholderTextColor="rgba(255,255,255,0.4)"
                style={styles.input}
                secureTextEntry
                editable={canEdit}
              />
            </View>

            <View style={styles.fieldWrap}>
              <Text style={styles.label}>Server Name</Text>
              <TextInput
                value={server}
                onChangeText={setServer}
                placeholder="Exness-MT5Real11 or ICMarketsSC-MT5"
                placeholderTextColor="rgba(255,255,255,0.4)"
                style={styles.input}
                editable={canEdit}
                autoCapitalize="none"
              />
            </View>

            <Pressable
              style={[styles.saveButton, (loading || isVerifying) && styles.saveButtonDisabled]}
              onPress={handleSaveMT5}
              disabled={loading || isVerifying}
            >
              {loading ? (
                <ActivityIndicator color="#062B3D" size="small" />
              ) : (
                <>
                  <Ionicons name="cloud-upload-outline" size={18} color="#062B3D" />
                  <Text style={styles.saveButtonText}>
                    {isVerifying ? "Verifying..." : "Save & Verify Connection"}
                  </Text>
                </>
              )}
            </Pressable>
          </GlassCard>

          {/* Status Card */}
          <GlassCard style={styles.statusCard}>
            <Text style={styles.statusTitle}>CONNECTION STATUS</Text>

            {statusLoading ? (
              <View style={styles.statusLoadingWrap}>
                <ActivityIndicator color={Colors.primary} size="large" />
                <Text style={styles.loadingText}>Checking MT5 connection...</Text>
              </View>
            ) : (
              <>
                <View style={styles.statusRow}>
                  <Text style={styles.statusLabel}>Status</Text>
                  <Text style={[styles.statusValue, { color: statusColor }]}>
                    {statusLabel}
                  </Text>
                </View>

                <View style={[styles.messageBox, isFailed && styles.messageBoxFailed]}>
                  <Text style={[styles.messageText, isFailed && styles.messageTextFailed]}>
                    {isFailed
                      ? getFriendlyMt5ErrorMessage(mt5Status?.message)
                      : mt5Status?.message || "No additional information."}
                  </Text>
                </View>

                {isConnected && mt5Status && (
                  <>
                    <View style={styles.statusRow}>
                      <Text style={styles.statusLabel}>Account Name</Text>
                      <Text style={styles.statusValue}>{mt5Status.account_name || "—"}</Text>
                    </View>

                    <View style={styles.statusRow}>
                      <Text style={styles.statusLabel}>Broker</Text>
                      <Text style={styles.statusValue}>{mt5Status.broker_name || "—"}</Text>
                    </View>

                    <View style={styles.statusRow}>
                      <Text style={styles.statusLabel}>Balance</Text>
                      <Text style={styles.statusValue}>
                        {mt5Status.balance ? `$${mt5Status.balance}` : "—"}
                      </Text>
                    </View>

                    {mt5Status.equity && (
                      <View style={styles.statusRow}>
                        <Text style={styles.statusLabel}>Equity</Text>
                        <Text style={styles.statusValue}>${mt5Status.equity}</Text>
                      </View>
                    )}

                    {mt5Status.last_verified_at && (
                      <View style={styles.statusRow}>
                        <Text style={styles.statusLabel}>Last Verified</Text>
                        <Text style={styles.statusValue}>
                          {new Date(mt5Status.last_verified_at).toLocaleString()}
                        </Text>
                      </View>
                    )}
                  </>
                )}
              </>
            )}
          </GlassCard>
        </ScrollView>
      </SafeAreaView>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safe: { flex: 1 },
  content: {
    padding: Spacing.medium,
    paddingBottom: 40,
    gap: Spacing.medium,
  },

  formCard: {
    borderRadius: 28,
    padding: 24,
    backgroundColor: "rgba(255,255,255,0.06)",
    borderColor: "rgba(255,255,255,0.12)",
    borderWidth: 1,
  },
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 24,
  },
  logoWrap: {
    alignItems: "center",
  },
  logo: {
    width: 140,
    height: 80,
  },

  fieldWrap: { marginBottom: 20 },
  label: {
    color: "rgba(240,248,255,0.92)",
    fontSize: 14,
    fontWeight: "700",
    marginBottom: 8,
  },
  input: {
    minHeight: 58,
    borderRadius: 18,
    paddingHorizontal: 18,
    color: "#FFFFFF",
    fontSize: 16,
    fontWeight: "700",
    backgroundColor: "rgba(255,255,255,0.08)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.13)",
  },

  saveButton: {
    marginTop: 12,
    minHeight: 58,
    borderRadius: 18,
    backgroundColor: "#1ed1e1",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
  },
  saveButtonDisabled: { opacity: 0.75 },
  saveButtonText: {
    color: "#062B3D",
    fontSize: 16,
    fontWeight: "900",
  },

  statusCard: {
    borderRadius: 28,
    padding: 24,
    backgroundColor: "rgba(255,255,255,0.06)",
    borderColor: "rgba(255,255,255,0.12)",
    borderWidth: 1,
  },
  statusTitle: {
    color: "#F6FBFF",
    fontSize: 18,
    fontWeight: "900",
    marginBottom: 20,
  },
  statusLoadingWrap: {
    minHeight: 140,
    alignItems: "center",
    justifyContent: "center",
    gap: 12,
  },
  loadingText: {
    color: "rgba(255,255,255,0.6)",
    fontSize: 13,
    fontWeight: "600",
  },

  statusRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 14,
  },
  statusLabel: {
    color: "rgba(214,234,255,0.78)",
    fontSize: 14,
    fontWeight: "600",
  },
  statusValue: {
    color: "#FFFFFF",
    fontSize: 14,
    fontWeight: "800",
    textAlign: "right",
  },

  messageBox: {
    marginVertical: 12,
    padding: 14,
    borderRadius: 14,
    backgroundColor: "rgba(255,255,255,0.06)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.1)",
  },
  messageBoxFailed: {
    borderColor: "#FF6B6B",
    backgroundColor: "rgba(255,107,107,0.08)",
  },
  messageText: {
    color: "rgba(230,240,255,0.88)",
    fontSize: 13,
    lineHeight: 19,
    fontWeight: "600",
  },
  messageTextFailed: {
    color: "#FF6B6B",
  },
});
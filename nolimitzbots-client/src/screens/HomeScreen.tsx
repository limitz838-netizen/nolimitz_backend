import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Animated,
  Dimensions,
  Easing,
  Image,
  ImageBackground,
  PanResponder,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons, MaterialCommunityIcons } from "@expo/vector-icons";
import MaskedView from "@react-native-masked-view/masked-view";
import * as Notifications from "expo-notifications";
import * as Device from "expo-device";

import GlassCard from "../components/GlassCard";
import { Colors } from "../theme/colors";
import { Radius } from "../theme/radius";
import { Spacing } from "../theme/spacing";

type HomeScreenProps = {
  licenseKey: string;
  onOpenSymbols?: () => void;
  onOpenMT5?: () => void;
  onAddRobot?: () => void;
  onDeleteRobot?: () => void;
  mt5Connected?: boolean;
  symbolsConfigured?: boolean;
  activeUntil?: string;
  ownerName?: string;
  ownerContact?: string;
  robotName?: string;
  logoUrl?: string;
  companyName?: string;
};

type TradeHistoryItem = {
  id: number;
  symbol: string;
  action: string | null;
  event_type: string;
  status: string;
  lot_size: string | null;
  price: string | null;
  sl: string | null;
  tp: string | null;
  comment: string | null;
  error_message: string | null;
  client_ticket: string | null;
  master_ticket: string;
  created_at: string;
};

const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_URL || "https://nolimitz-backend-yfne.onrender.com";

const { width: SCREEN_WIDTH, height: SCREEN_HEIGHT } = Dimensions.get("window");

const FLOAT_SIZE = 28;
const FLOAT_RADIUS = 12;
const FLOAT_MARGIN = 14;
const PANEL_WIDTH = 300;
const PANEL_HEIGHT_OFFSET = 210;

const clamp = (value: number, min: number, max: number) =>
  Math.min(Math.max(value, min), max);

export default function HomeScreen({
  licenseKey,
  onOpenSymbols,
  onOpenMT5,
  onAddRobot,
  onDeleteRobot,
  mt5Connected = false,
  symbolsConfigured = false,
  activeUntil = "",
  ownerName = "",
  ownerContact = "",
  robotName = "",
  logoUrl = "",
  companyName = "",
}: HomeScreenProps) {
  const [isRunning, setIsRunning] = useState(false);
  const [showFloatingPanel, setShowFloatingPanel] = useState(false);
  const [showLogs, setShowLogs] = useState(false);
  const [tradeHistory, setTradeHistory] = useState<TradeHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const initialX = SCREEN_WIDTH - FLOAT_SIZE - FLOAT_MARGIN;
  const initialY = SCREEN_HEIGHT * 0.48;

  const pan = useRef(new Animated.ValueXY({ x: initialX, y: initialY })).current;
  const lastOffset = useRef({ x: initialX, y: initialY });
  const movedRef = useRef(false);
  const activeNotificationId = useRef<string | null>(null);

  const statusAnim = useRef(new Animated.Value(0)).current;

  const canStartRobot = Boolean(licenseKey?.trim()) && mt5Connected && symbolsConfigured;

  // ==================== TRADE HISTORY ====================

  const formatHistoryTime = (value?: string | null): string => {
    if (!value) return "--:--";
    try {
      return new Date(value).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return "--:--";
    }
  };

  const buildHistoryLine = (item: TradeHistoryItem): string => {
    const symbol = item.symbol?.trim() || "UNKNOWN";
    const action = item.action?.trim().toUpperCase() || "";
    const eventType = item.event_type?.toLowerCase().trim() || "";
    const status = item.status?.toLowerCase().trim() || "";
    const errorMsg = item.error_message?.trim();

    // Failed trades
    if (status === "failed") {
      let cleanError = "execution failed";

      if (errorMsg) {
        const msg = errorMsg.toLowerCase();
        if (msg.includes("500") || msg.includes("internal")) cleanError = "server error";
        else if (msg.includes("timeout")) cleanError = "connection timeout";
        else if (msg.includes("network")) cleanError = "network issue";
        else if (msg.includes("login") || msg.includes("auth")) cleanError = "auth failed";
      }

      return `[FAIL] ${action ? action + " " : ""}${symbol} - ${cleanError}`;
    }

    // Open events
    if (eventType === "open") {
      if (["executed", "success", "processing"].includes(status)) {
        return `[EXEC] ${action ? action + " " : ""}${symbol} opened`;
      }
      if (status === "pending") {
        return `[WAIT] ${action ? action + " " : ""}${symbol} waiting`;
      }
      return `[OPEN] ${action ? action + " " : ""}${symbol} ${status}`;
    }

    // Modify / Close events
    if (eventType === "modify") return `[UPDATE] ${symbol}`;
    if (eventType === "close") return `[CLOSE] ${symbol} closed`;

    // Default fallback
    return `[${status.toUpperCase() || "UNKNOWN"}] ${action ? action + " " : ""}${symbol}`;
  };

  const fetchTradeHistory = async (showLoader = false) => {
    if (!licenseKey?.trim() || !isRunning) {
      setTradeHistory([]);
      return;
    }

    try {
      if (showLoader) setHistoryLoading(true);

      const response = await fetch(`${API_BASE_URL}/client/trade-history`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ license_key: licenseKey, limit: 30 }),
      });

      const text = await response.text();
      const data = text ? JSON.parse(text) : [];

      if (!response.ok) throw new Error(data?.detail || "Failed to fetch history");

      setTradeHistory(Array.isArray(data) ? data : []);
    } catch (error) {
      console.log("fetchTradeHistory error:", error);
      setTradeHistory([]);
    } finally {
      if (showLoader) setHistoryLoading(false);
    }
  };

  // ==================== EFFECTS ====================

  useEffect(() => {
    if (!isRunning) {
      setTradeHistory([]);
      return;
    }
    fetchTradeHistory(true);
  }, [isRunning, licenseKey]);

  useEffect(() => {
    if (!isRunning) return;

    const interval = setInterval(() => fetchTradeHistory(false), 4000);
    return () => clearInterval(interval);
  }, [isRunning, licenseKey]);

  // Notifications setup
  useEffect(() => {
    const setupNotifications = async () => {
      if (!Device.isDevice) return;

      try {
        let { status } = await Notifications.getPermissionsAsync();
        if (status !== "granted") {
          ({ status } = await Notifications.requestPermissionsAsync());
        }

        if (status !== "granted") return;

        if (Platform.OS === "android") {
          await Notifications.setNotificationChannelAsync("smart-execution", {
            name: "Smart Execution",
            importance: Notifications.AndroidImportance.HIGH,
            vibrationPattern: [0],
            lightColor: "#22D3EE",
            lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
          });
        }
      } catch (error) {
        console.log("Notification setup error:", error);
      }
    };

    setupNotifications();
  }, []);

  // Running animation
  useEffect(() => {
    if (!isRunning) {
      statusAnim.setValue(0);
      return;
    }

    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(statusAnim, { toValue: 1, duration: 1200, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
        Animated.timing(statusAnim, { toValue: 0, duration: 1200, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
      ])
    );

    loop.start();
    return () => loop.stop();
  }, [isRunning]);

  // ==================== NOTIFICATIONS ====================

  const showRunningNotification = async () => {
    try {
      if (activeNotificationId.current) {
        await Notifications.dismissNotificationAsync(activeNotificationId.current);
      }

      const id = await Notifications.scheduleNotificationAsync({
        content: {
          title: robotName || "NolimitzBots",
          body: "Smart Execution in progress",
          sound: false,
          data: { type: "smart_execution_running", licenseKey, robotName },
        },
        trigger: null,
      });

      activeNotificationId.current = id;
    } catch (error) {
      console.log("showRunningNotification error:", error);
    }
  };

  const hideRunningNotification = async () => {
    if (activeNotificationId.current) {
      await Notifications.dismissNotificationAsync(activeNotificationId.current).catch(() => {});
      activeNotificationId.current = null;
    }
  };

  // ==================== HANDLERS ====================

  const handleStartStop = () => {
  if (!isRunning) {
    if (!canStartRobot) {
      Alert.alert(
        "Setup Required",
        "Please add MT5 account and symbol setup first."
      );
      return;
    }

    setTradeHistory([]);
    setIsRunning(true);
    setShowFloatingPanel(false);
    setShowLogs(false);
    showRunningNotification();
    return;
  }

  Alert.alert("Stop Robot", "Are you sure you want to stop this robot?", [
    { text: "Cancel", style: "cancel" },
    {
      text: "Stop",
      style: "destructive",
      onPress: async () => {
        setIsRunning(false);
        setTradeHistory([]);
        setShowFloatingPanel(false);
        setShowLogs(false);
        await hideRunningNotification();
      },
    },
  ]);
};

  const handleRobotInfo = () => {
    Alert.alert(
      "Robot Information",
      `Status: ${isRunning ? "Active" : "Stopped"}\n\n` +
        `Robot Name: ${robotName || "—"}\n` +
        `License Key: ${licenseKey || "—"}\n` +
        `Active Until: ${activeUntil || "—"}\n` +
        `Owner: ${ownerName || "—"}\n` +
        `Contact: ${ownerContact || "—"}`
    );
  };

  const handleDelete = () => {
  Alert.alert(
    "Warning",
    "Are you sure you want to delete this robot? This device will no longer use the saved license key.",
    [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          setIsRunning(false);
          setTradeHistory([]);
          setShowFloatingPanel(false);
          setShowLogs(false);
          await hideRunningNotification();
          onDeleteRobot?.();
        },
      },
    ]
  );
};

  // ==================== PAN RESPONDER ====================

  const panResponder = useRef(
    PanResponder.create({
      onMoveShouldSetPanResponder: (_, gesture) =>
        Math.abs(gesture.dx) > 4 || Math.abs(gesture.dy) > 4,

      onPanResponderGrant: () => {
        movedRef.current = false;
      },

      onPanResponderMove: (_, gesture) => {
        movedRef.current = true;

        const nextX = clamp(
          lastOffset.current.x + gesture.dx,
          FLOAT_MARGIN,
          SCREEN_WIDTH - FLOAT_SIZE - FLOAT_MARGIN
        );
        const nextY = clamp(
          lastOffset.current.y + gesture.dy,
          80,
          SCREEN_HEIGHT - FLOAT_SIZE - 120
        );

        pan.setValue({ x: nextX, y: nextY });
      },

      onPanResponderRelease: (_, gesture) => {
        const nextX = clamp(
          lastOffset.current.x + gesture.dx,
          FLOAT_MARGIN,
          SCREEN_WIDTH - FLOAT_SIZE - FLOAT_MARGIN
        );
        const nextY = clamp(
          lastOffset.current.y + gesture.dy,
          80,
          SCREEN_HEIGHT - FLOAT_SIZE - 120
        );

        lastOffset.current = { x: nextX, y: nextY };

        Animated.spring(pan, {
          toValue: { x: nextX, y: nextY },
          useNativeDriver: false,
          tension: 90,
          friction: 10,
        }).start(() => {
          setTimeout(() => (movedRef.current = false), 100);
        });
      },
    })
  ).current;

  // ==================== ANIMATIONS ====================

  const animatedStatusOpacity = statusAnim.interpolate({
    inputRange: [0, 1],
    outputRange: [0.72, 1],
  });

  const animatedStatusScale = statusAnim.interpolate({
    inputRange: [0, 1],
    outputRange: [0.98, 1.03],
  });

  const panelLeft = clamp(
    lastOffset.current.x - (PANEL_WIDTH - FLOAT_SIZE),
    12,
    SCREEN_WIDTH - PANEL_WIDTH - 12
  );

  const panelTop = clamp(
    lastOffset.current.y - PANEL_HEIGHT_OFFSET,
    110,
    SCREEN_HEIGHT - 360
  );

  const aiPanelBackground =
    "https://images.unsplash.com/photo-1677442136019-21780ecad995?q=80&w=1200&auto=format&fit=crop";

  return (
    <LinearGradient
      colors={["#050B18", "#09162E", "#0D2A57"]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.container}
    >
      <SafeAreaView style={styles.safe}>
        <ScrollView
  contentContainerStyle={styles.content}
  showsVerticalScrollIndicator={false}
  scrollEnabled={false}
  bounces={false}
>
          {/* Top Row */}
          <View style={styles.topRow}>
            <View style={styles.companyPill}>
              <MaskedView maskElement={<Text style={styles.companyPillText}>{companyName || "NolimitzBots"}</Text>}>
                <LinearGradient colors={["#FFD700", "#FF8C00"]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }}>
                  <Text style={[styles.companyPillText, { opacity: 0 }]}>{companyName || "NolimitzBots"}</Text>
                </LinearGradient>
              </MaskedView>
            </View>

            <Pressable style={styles.infoButton} onPress={handleRobotInfo}>
              <Ionicons name="information-circle-outline" size={18} color={Colors.text} />
            </Pressable>
          </View>

          {/* Hero Card */}
          <GlassCard style={styles.heroCard}>
            <View style={styles.heroMediaWrap}>
              <Image
                source={{ uri: logoUrl || "https://images.unsplash.com/photo-1677442135703-1787eea5ce01?q=80&w=1400&auto=format&fit=crop" }}
                style={styles.heroImage}
                resizeMode="cover"
              />

              <LinearGradient
                colors={["rgba(2,6,16,0.08)", "rgba(2,6,16,0.24)", "rgba(2,6,16,0.66)", "rgba(2,6,16,0.96)"]}
                style={styles.heroOverlay}
              >
                {/* Smart Execution Badge */}
                <Pressable
                  style={[
                    styles.smartExecutionBadge,
                    isRunning ? styles.smartExecutionBadgeOn : styles.smartExecutionBadgeOff,
                  ]}
                  onPress={handleStartStop}
                >
                  <MaterialCommunityIcons
                    name="robot-outline"
                    size={14}
                    color={isRunning ? "#67FFC8" : "#FFB4B4"}
                  />
                  <View style={[styles.smartExecutionDot, { backgroundColor: isRunning ? "#67FFC8" : "#FF9A9A" }]} />
                  <Text style={[styles.smartExecutionText, { color: isRunning ? "#67FFC8" : "#FFB4B4" }]}>
                    Smart Execution
                  </Text>
                </Pressable>

                <View style={styles.heroBottomContent}>
                  <MaskedView
                    maskElement={<Text style={styles.robotTitleGradient}>{robotName || "SUPER EA"}</Text>}
                  >
                    <LinearGradient colors={["#22D3EE", "#3B82F6", "#8B5CF6"]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}>
                      <Text style={[styles.robotTitleGradient, { opacity: 0 }]}>{robotName || "SUPER EA"}</Text>
                    </LinearGradient>
                  </MaskedView>

                  <Text style={styles.robotSubtitleSmall}>Powered by NolimitzBots</Text>

                  {/* Action Buttons */}
                  <View style={styles.actionContainer}>
                    {[
                      { label: isRunning ? "Stop" : "Start", icon: isRunning ? "pause" : "play", color: "#00FFA3", onPress: handleStartStop },
                      { label: "Symbols", icon: "options-outline", color: "#4FD1FF", onPress: onOpenSymbols },
                      { label: "Delete", icon: "trash-outline", color: "#FF6B6B", onPress: handleDelete },
                    ].map((btn, index) => (
                      <Pressable key={index} style={styles.actionItemClean} onPress={btn.onPress}>
                        <Ionicons name={btn.icon as any} size={20} color={btn.color} />
                        <Text style={[styles.actionTextClean, { color: btn.color }]}>{btn.label}</Text>
                      </Pressable>
                    ))}
                  </View>
                </View>
              </LinearGradient>
            </View>
          </GlassCard>

          {/* My Robots Section */}
          <View style={styles.sectionRow}>
            <Text style={styles.sectionTitle}>My Robots</Text>
          </View>

          <GlassCard style={styles.robotListCard}>
            <View style={styles.robotListInner}>
              <View style={styles.robotMiniLogo}>
                <MaterialCommunityIcons name="robot-outline" size={20} color="#22D3EE" />
              </View>
              <View style={styles.robotTextWrap}>
                <Text style={styles.robotListName}>{robotName || "—"}</Text>
              </View>
              <Ionicons name="chevron-forward-outline" size={20} color={Colors.subText} />
            </View>
          </GlassCard>

          <Pressable style={styles.addRobotCard} onPress={onAddRobot}>
            <GlassCard style={styles.addRobotGlass}>
              <View style={styles.addRobotLeft}>
                <Text style={styles.addRobotTitle}>Add New Robot</Text>
                <Text style={styles.addRobotSubtitle}>You must have a valid license key</Text>
              </View>
              <View style={styles.addRobotPlusWrap}>
                <Ionicons name="add" size={24} color="#072B42" />
              </View>
            </GlassCard>
          </Pressable>
        </ScrollView>

        {/* Floating Robot Button + Panel */}
        {isRunning && (
          <>
            <Animated.View
              style={[styles.floatingRoot, { transform: [{ translateX: pan.x }, { translateY: pan.y }] }]}
              {...panResponder.panHandlers}
            >
              <Pressable
                style={styles.floatingRobotButton}
                onPress={() => {
                  if (movedRef.current) {
                    movedRef.current = false;
                    return;
                  }
                  setShowFloatingPanel((prev) => {
                    const next = !prev;
                    if (next) fetchTradeHistory(false);
                    return next;
                  });
                }}
              >
                {logoUrl ? (
                  <Image source={{ uri: logoUrl }} style={styles.floatingLogoImage} resizeMode="cover" />
                ) : (
                  <View style={styles.floatingFallbackWrap}>
                    <Ionicons name="flash" size={16} color="#9FE8FF" />
                  </View>
                )}
              </Pressable>
            </Animated.View>

            {showFloatingPanel && (
              <View
                pointerEvents="box-none"
                style={[styles.detachedFloatingPanelWrap, { left: panelLeft, top: panelTop }]}
              >
                <GlassCard style={styles.floatingPanel}>
                  {/* Running Status */}
                  <Animated.View style={[styles.runningStatusWrap, { opacity: animatedStatusOpacity, transform: [{ scale: animatedStatusScale }] }]}>
                    <LinearGradient
                      colors={["rgba(91,255,201,0.12)", "rgba(110,180,255,0.22)", "rgba(91,255,201,0.12)"]}
                      start={{ x: 0, y: 0 }}
                      end={{ x: 1, y: 0 }}
                      style={styles.runningStatusGradient}
                    >
                      <View style={styles.serverDot} />
                      <Text style={styles.runningStatusText}>Smart Execution in progress..</Text>
                    </LinearGradient>
                  </Animated.View>

                  {/* Hero Image */}
                  <ImageBackground source={{ uri: aiPanelBackground }} style={styles.panelHero} imageStyle={styles.panelHeroImage}>
                    <LinearGradient
                      colors={["rgba(4,12,28,0.48)", "rgba(4,12,28,0.70)", "rgba(4,12,28,0.92)"]}
                      style={styles.panelHeroOverlay}
                    >
                      <Pressable style={styles.floatingCloseButton} onPress={() => { setShowFloatingPanel(false); setShowLogs(false); }}>
                        <Ionicons name="close" size={14} color="rgba(255,255,255,0.75)" />
                      </Pressable>

                      <Text style={styles.floatingRobotName}>{robotName || "—"}</Text>
                      <Text style={styles.floatingPoweredBy}>Powered by NolimitzBots</Text>
                    </LinearGradient>
                  </ImageBackground>

                  {/* Action Buttons */}
                  <View style={styles.floatingActions}>
                    <Pressable style={[styles.panelActionButton, styles.panelStopButton]} onPress={handleStartStop}>
                      <Ionicons name="stop" size={15} color="#F7D5E0" />
                      <Text style={[styles.panelActionText, styles.stopText]}>Stop</Text>
                    </Pressable>

                    <Pressable
                      style={styles.panelActionButton}
                      onPress={() => {
                        setShowLogs((prev) => {
                          const next = !prev;
                          if (next) fetchTradeHistory(false);
                          return next;
                        });
                      }}
                    >
                      <Ionicons name={showLogs ? "chevron-up" : "ellipsis-horizontal"} size={16} color="#DCE8FF" />
                      <Text style={styles.panelActionText}>{showLogs ? "Less" : "More"}</Text>
                    </Pressable>
                  </View>

                  {/* Logs */}
                  {showLogs && (
                    <View style={styles.logsWrap}>
                      <Text style={styles.logsTitle}>Trade History Logs</Text>

                      {historyLoading ? (
                        <Text style={styles.emptyLogsText}>Loading history...</Text>
                      ) : tradeHistory.length === 0 ? (
                        <Text style={styles.emptyLogsText}>No history yet</Text>
                      ) : (
                        <ScrollView style={styles.logsScroll} contentContainerStyle={styles.logsScrollContent}>
                          {tradeHistory.map((item) => (
                            <View key={item.id} style={styles.logItem}>
                              <Text style={styles.logPrompt}>{formatHistoryTime(item.created_at)}</Text>
                              <Text style={styles.logText}>{buildHistoryLine(item)}</Text>
                            </View>
                          ))}
                        </ScrollView>
                      )}
                    </View>
                  )}
                </GlassCard>
              </View>
            )}
          </>
        )}
      </SafeAreaView>
    </LinearGradient>
  );
}

// ==================== STYLES ====================

const styles = StyleSheet.create({
  container: { flex: 1 },
  safe: { flex: 1 },
  content: {
    padding: Spacing.medium,
    paddingBottom: 40,
    gap: Spacing.medium,
  },

  topRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 2,
  },
  companyPill: {
    alignSelf: "flex-start",
    backgroundColor: "rgba(255,255,255,0.08)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.12)",
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
  },
  companyPillText: {
    color: "#FFD700",
    fontSize: 12,
    fontWeight: "700",
  },
  infoButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: "rgba(255,255,255,0.08)",
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.10)",
  },

  heroCard: { borderRadius: 28, overflow: "hidden", padding: 0 },
  heroMediaWrap: {
  height: 380,
  borderRadius: 28,
  overflow: "hidden",
  position: "relative",
  backgroundColor: "rgba(255,255,255,0.03)",
  alignItems: "center",
  justifyContent: "center",
},

heroImage: {
  position: "absolute",
  width: "100%",
  height: "100%",
  alignSelf: "center",
},
  heroOverlay: { flex: 1, justifyContent: "space-between", padding: 18 },

  smartExecutionBadge: {
    alignSelf: "flex-start",
    marginTop: 6,
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: "rgba(12,18,34,0.45)",
  },
  smartExecutionBadgeOn: { borderColor: "rgba(0,255,163,0.30)" },
  smartExecutionBadgeOff: { borderColor: "rgba(255,110,110,0.28)" },
  smartExecutionDot: { width: 7, height: 7, borderRadius: 10, marginRight: 8 },
  smartExecutionText: { fontSize: 12, fontWeight: "800" },

  heroBottomContent: { marginTop: "auto" },
  robotTitleGradient: {
  fontSize: 50,
  fontWeight: "800",
  textAlign: "center",
  letterSpacing: 1.2,
  textTransform: "uppercase",
  fontStyle: "italic",
  textShadowColor: "rgba(0,0,0,0.25)",
  textShadowOffset: { width: 0, height: 2 },
  textShadowRadius: 10,
},

  robotSubtitleSmall: {
  fontSize: 12,
  color: "rgba(142, 141, 141, 0.72)",
  textAlign: "center",
  marginTop: 2,
  letterSpacing: 0.4,
  fontWeight: "600",
},

  actionContainer: {
    flexDirection: "row",
    marginTop: 16,
    borderRadius: 22,
    paddingVertical: 16,
    backgroundColor: "rgba(255,255,255,0.05)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.10)",
    justifyContent: "space-around",
  },
  actionItemClean: { alignItems: "center", justifyContent: "center", gap: 6 },
  actionTextClean: { fontSize: 13, fontWeight: "600" },

  sectionRow: { marginTop: 4 },
  sectionTitle: { color: Colors.text, fontSize: 20, fontWeight: "800", marginBottom: 14 },

  robotListCard: { padding: 16 },
  robotListInner: { flexDirection: "row", alignItems: "center" },
  robotMiniLogo: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: "rgba(255,255,255,0.05)",
    alignItems: "center",
    justifyContent: "center",
    marginRight: 14,
  },
  robotTextWrap: { flex: 1 },
  robotListName: { color: Colors.text, fontSize: 17, fontWeight: "800" },

  addRobotCard: { marginTop: 2 },
  addRobotGlass: {
    minHeight: 78,
    borderRadius: 22,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 18,
    paddingVertical: 14,
  },
  addRobotLeft: { flex: 1, paddingRight: 12 },
  addRobotTitle: { color: Colors.text, fontSize: 16, fontWeight: "900", marginBottom: 4 },
  addRobotSubtitle: { color: Colors.subText, fontSize: 13, fontWeight: "600" },
  addRobotPlusWrap: {
    width: 50,
    height: 50,
    borderRadius: 16,
    backgroundColor: "#8BFFB8",
    alignItems: "center",
    justifyContent: "center",
  },

  // Floating Elements
  floatingRoot: { position: "absolute", top: 0, left: 0, zIndex: 9999, elevation: 9999 },
  floatingRobotButton: {
    width: FLOAT_SIZE,
    height: FLOAT_SIZE,
    borderRadius: FLOAT_RADIUS,
    backgroundColor: "rgba(255,255,255,0.04)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.14)",
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden",
  },
  floatingLogoImage: { width: "100%", height: "100%" },
  floatingFallbackWrap: {
    width: "100%",
    height: "100%",
    backgroundColor: "rgba(255,255,255,0.06)",
    alignItems: "center",
    justifyContent: "center",
  },

  detachedFloatingPanelWrap: { position: "absolute", width: PANEL_WIDTH, zIndex: 9998, elevation: 9998 },
  floatingPanel: {
    borderRadius: 24,
    padding: 14,
    backgroundColor: "rgba(6,16,38,0.96)",
    borderColor: "rgba(255,255,255,0.10)",
  },

  runningStatusWrap: { marginBottom: 12 },
  runningStatusGradient: {
    minHeight: 38,
    borderRadius: 14,
    paddingHorizontal: 12,
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderColor: "rgba(105,255,214,0.18)",
  },
  runningStatusText: { color: "#C7FFE9", fontSize: 12, fontWeight: "900" },
  serverDot: { width: 9, height: 9, borderRadius: 9, backgroundColor: "#8AFFF0", marginRight: 10 },

  panelHero: { minHeight: 104, borderRadius: 18, overflow: "hidden", marginBottom: 12 },
  panelHeroImage: { borderRadius: 18 },
  panelHeroOverlay: { flex: 1, padding: 14, justifyContent: "flex-end" },
  floatingCloseButton: {
    position: "absolute",
    top: 10,
    right: 10,
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: "rgba(255,255,255,0.10)",
    alignItems: "center",
    justifyContent: "center",
  },
  floatingRobotName: { color: "#FFFFFF", fontSize: 18, fontWeight: "900", marginBottom: 3 },
  floatingPoweredBy: { color: "rgba(255,255,255,0.82)", fontSize: 11, fontWeight: "700" },

  floatingActions: { flexDirection: "row", gap: 10, marginBottom: 6 },
  panelActionButton: {
    flex: 1,
    minHeight: 48,
    borderRadius: 16,
    backgroundColor: "rgba(255,255,255,0.06)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.10)",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  panelStopButton: {
    backgroundColor: "rgba(255,120,145,0.08)",
    borderColor: "rgba(255,120,145,0.28)",
  },
  panelActionText: { color: "#DCE8FF", fontSize: 13, fontWeight: "800" },
  stopText: { color: "#FFD7E1" },

  logsWrap: { marginTop: 6, paddingTop: 12, borderTopWidth: 1, borderTopColor: "rgba(255,255,255,0.08)" },
  logsTitle: { color: "#F7FBFF", fontSize: 13, fontWeight: "900", marginBottom: 10 },
  logsScroll: { maxHeight: 190 },
  logsScrollContent: { paddingBottom: 4 },
  emptyLogsText: {
    color: "rgba(220,232,255,0.68)",
    fontSize: 13,
    fontWeight: "600",
    fontStyle: "italic",
    marginTop: 8,
  },
  logItem: { flexDirection: "row", alignItems: "flex-start", marginBottom: 7, gap: 8 },
  logPrompt: {
    color: "#7FFFE1",
    fontSize: 10,
    lineHeight: 18,
    fontWeight: "800",
    fontFamily: "monospace",
    marginTop: 1,
  },
  logText: {
    flex: 1,
    color: "#D8F3FF",
    fontSize: 12,
    lineHeight: 18,
    fontWeight: "700",
    fontFamily: "monospace",
  },
});
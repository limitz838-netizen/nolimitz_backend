import React, { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons, MaterialCommunityIcons } from "@expo/vector-icons";

import HomeScreen from "../screens/HomeScreen";
import MT5Screen from "../screens/MT5Screen";
import SymbolsScreen from "../screens/SymbolsScreen";
import { Colors } from "../theme/colors";

type TabType = "home" | "mt5";
type OverlayType = "none" | "symbols";

type MainTabsProps = {
  licenseKey: string;
  robotName?: string;
  activeUntil?: string;
  ownerName?: string;
  ownerContact?: string;
  logoUrl?: string;
  companyName?: string;
  onAddRobot: () => void;
  onDeleteRobot: () => void;
};

export default function MainTabs({
  licenseKey,
  robotName,
  activeUntil,
  ownerName,
  ownerContact,
  logoUrl,
  companyName,
  onAddRobot,
  onDeleteRobot,
}: MainTabsProps) {
  const [activeTab, setActiveTab] = useState<TabType>("home");
  const [overlayScreen, setOverlayScreen] = useState<OverlayType>("none");

  const [mt5Connected, setMT5Connected] = useState(false);
  const [symbolsConfigured, setSymbolsConfigured] = useState(false);

  const openSymbols = () => setOverlayScreen("symbols");
  const closeOverlay = () => setOverlayScreen("none");

  if (overlayScreen === "symbols") {
    return (
      <SymbolsScreen
        licenseKey={licenseKey}
        onBack={closeOverlay}
        onSymbolsStatusChange={setSymbolsConfigured}
      />
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <View style={styles.container}>
        <View style={styles.content}>
          {activeTab === "home" ? (
            <HomeScreen
              licenseKey={licenseKey}
              robotName={robotName}
              activeUntil={activeUntil}
              ownerName={ownerName}
              ownerContact={ownerContact}
              logoUrl={logoUrl}
              companyName={companyName}
              mt5Connected={mt5Connected}
              symbolsConfigured={symbolsConfigured}
              onOpenSymbols={openSymbols}
              onOpenMT5={() => setActiveTab("mt5")}
              onAddRobot={onAddRobot}
              onDeleteRobot={onDeleteRobot}
            />
          ) : (
            <MT5Screen
              licenseKey={licenseKey}
              onMT5StatusChange={setMT5Connected}
            />
          )}
        </View>

        <View style={styles.tabBarWrap}>
          <View style={styles.tabBar}>
            <Pressable
              style={[
                styles.tabItem,
                activeTab === "home" && styles.tabItemActive,
              ]}
              onPress={() => setActiveTab("home")}
            >
              <Ionicons
                name={activeTab === "home" ? "home" : "home-outline"}
                size={20}
                color={activeTab === "home" ? Colors.primary : Colors.subText}
              />
              <Text
                style={[
                  styles.tabLabel,
                  activeTab === "home" && styles.tabLabelActive,
                ]}
              >
                Home
              </Text>
            </Pressable>

            <Pressable
              style={[
                styles.tabItem,
                activeTab === "mt5" && styles.tabItemActive,
              ]}
              onPress={() => setActiveTab("mt5")}
            >
              <MaterialCommunityIcons
                name={activeTab === "mt5" ? "chart-line" : "chart-line-variant"}
                size={21}
                color={activeTab === "mt5" ? Colors.primary : Colors.subText}
              />
              <Text
                style={[
                  styles.tabLabel,
                  activeTab === "mt5" && styles.tabLabelActive,
                ]}
              >
                MetaTrader
              </Text>
            </Pressable>
          </View>
        </View>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: "#050B18",
  },
  container: {
    flex: 1,
    backgroundColor: "#050B18",
  },
  content: {
    flex: 1,
  },
  tabBarWrap: {
    paddingHorizontal: 14,
    paddingTop: 6,
    paddingBottom: 10,
    backgroundColor: "rgba(5,11,24,0.98)",
  },
  tabBar: {
    flexDirection: "row",
    backgroundColor: "rgba(255,255,255,0.06)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.10)",
    borderRadius: 24,
    paddingVertical: 8,
    paddingHorizontal: 8,
  },
  tabItem: {
    flex: 1,
    minHeight: 58,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
  },
  tabItemActive: {
    backgroundColor: "rgba(255,255,255,0.04)",
  },
  tabLabel: {
    color: Colors.subText,
    fontSize: 12,
    fontWeight: "700",
  },
  tabLabelActive: {
    color: Colors.primary,
  },
});
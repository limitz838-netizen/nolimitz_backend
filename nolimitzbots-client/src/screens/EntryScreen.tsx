import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

import GlassCard from "../components/GlassCard";
import { Colors } from "../theme/colors";
import { Spacing } from "../theme/spacing";

type EntryScreenProps = {
  onAddEA: () => void;
};

export default function EntryScreen({ onAddEA }: EntryScreenProps) {
  return (
    <LinearGradient
      colors={["#050B18", "#09162E", "#0D2A57"]}
      style={styles.container}
    >
      <SafeAreaView style={styles.safe}>
        <View style={styles.content}>
          <Pressable onPress={onAddEA}>
            <GlassCard style={styles.addCard}>
              <View style={styles.iconWrap}>
                <Ionicons name="add" size={26} color="#08344C" />
              </View>

              <View style={styles.textWrap}>
                <Text style={styles.title}>Add a new EA</Text>
                <Text style={styles.subtitle}>
                  You must have a valid license key
                </Text>
              </View>
            </GlassCard>
          </Pressable>
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
    justifyContent: "flex-start",
    paddingHorizontal: Spacing.medium,
    paddingTop: 80,
  },
  addCard: {
    flexDirection: "row",
    alignItems: "center",
    padding: 16,
    borderRadius: 24,
    backgroundColor: "rgba(255,255,255,0.06)",
    borderColor: "rgba(255,255,255,0.12)",
  },
  iconWrap: {
    width: 54,
    height: 54,
    borderRadius: 16,
    backgroundColor: "#44cff1",
    alignItems: "center",
    justifyContent: "center",
    marginRight: 14,
  },
  textWrap: {
    flex: 1,
  },
  title: {
    color: Colors.text,
    fontSize: 17,
    fontWeight: "900",
  },
  subtitle: {
    color: Colors.text,
    opacity: 0.8,
    fontSize: 13,
    marginTop: 4,
    fontWeight: "600",
  },
});
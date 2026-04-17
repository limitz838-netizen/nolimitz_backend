import React from "react";
import { StyleSheet, View, ViewStyle } from "react-native";
import { Colors } from "../theme/colors";
import { Radius } from "../theme/radius";

type Props = {
  children: React.ReactNode;
  style?: ViewStyle | ViewStyle[];
};

export default function GlassCard({ children, style }: Props) {
  return <View style={[styles.card, style]}>{children}</View>;
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: Colors.glass,
    borderRadius: Radius.large,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
    overflow: "hidden",
  },
});
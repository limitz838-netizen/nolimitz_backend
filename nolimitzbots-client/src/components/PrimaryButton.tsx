import React from "react";
import { Pressable, StyleSheet, Text } from "react-native";
import { Colors } from "../theme/colors";
import { Radius } from "../theme/radius";
import { Spacing } from "../theme/spacing";

type Props = {
  title: string;
  onPress?: () => void;
};

export default function PrimaryButton({ title, onPress }: Props) {
  return (
    <Pressable style={styles.button} onPress={onPress}>
      <Text style={styles.text}>{title}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    backgroundColor: "rgba(0,255,163,0.15)",
    borderWidth: 1,
    borderColor: "rgba(0,255,163,0.35)",
    borderRadius: Radius.medium,
    paddingVertical: Spacing.medium,
    alignItems: "center",
  },
  text: {
    color: Colors.text,
    fontSize: 16,
    fontWeight: "700",
  },
});
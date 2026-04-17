import React from "react";
import { NavigationContainer } from "@react-navigation/native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { Ionicons } from "@expo/vector-icons";
import HomeScreen from "../screens/HomeScreen";
import MT5Screen from "../screens/MT5Screen";
import { Colors } from "../theme/colors";

const Tab = createBottomTabNavigator();

export default function AppNavigator() {
  return (
    <NavigationContainer>
      <Tab.Navigator
        screenOptions={({ route }) => ({
          headerShown: false,
          tabBarStyle: {
            backgroundColor: "#0B1422",
            borderTopColor: "rgba(255,255,255,0.06)",
            height: 70,
            paddingBottom: 8,
            paddingTop: 8,
          },
          tabBarActiveTintColor: Colors.primary,
          tabBarInactiveTintColor: Colors.subText,
          tabBarIcon: ({ color, size }) => {
            const iconName = route.name === "Home" ? "home" : "phone-portrait";
            return <Ionicons name={iconName as any} size={size} color={color} />;
          },
        })}
      >
        <Tab.Screen name="Home" component={HomeScreen} />
        <Tab.Screen name="Metatrader" component={MT5Screen} />
      </Tab.Navigator>
    </NavigationContainer>
  );
}
import { Tabs } from 'expo-router/js-tabs';
import { StatusBar } from 'expo-status-bar';
import { Text, type ColorValue } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { colors } from '../components/ui';
import { FitnessProvider } from '../lib/fitness';
import { SupportProvider } from '../lib/support';

const icon = (glyph: string) =>
  function TabIcon({ color }: { color: ColorValue }) {
    return <Text style={{ color, fontSize: 18 }}>{glyph}</Text>;
  };

function AppTabs() {
  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: colors.primary,
        // Android is edge-to-edge: without this the tab bar rides up above the keyboard under the input.
        tabBarHideOnKeyboard: true,
        headerStyle: { backgroundColor: colors.bg },
        headerShadowVisible: false,
        sceneStyle: { backgroundColor: colors.bg },
      }}
    >
      <Tabs.Screen name="index" options={{ title: 'Home', tabBarIcon: icon('⌂') }} />
      <Tabs.Screen name="workouts" options={{ title: 'Workouts', tabBarIcon: icon('🏋') }} />
      <Tabs.Screen name="chat" options={{ title: 'Assistant', tabBarIcon: icon('💬') }} />
    </Tabs>
  );
}

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <FitnessProvider>
        <SupportProvider>
          <AppTabs />
        </SupportProvider>
      </FitnessProvider>
    </SafeAreaProvider>
  );
}

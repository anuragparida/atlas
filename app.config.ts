import type { ExpoConfig, ConfigContext } from "expo/config";

// SDK 51's `ExpoConfig` type doesn't include `newArchEnabled` yet, but
// the runtime reads it from the top level. Cast to `any` for the merge
// step so `tsc --noEmit` stays clean — see README §3.
const sdk51Flags = { newArchEnabled: true } as Record<string, unknown>;

export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: "Atlas Chat",
  slug: "atlas-chat",
  scheme: "atlaschat",
  version: "0.1.0",
  orientation: "portrait",
  icon: "./assets/icon.png",
  userInterfaceStyle: "dark",
  ...(sdk51Flags as Partial<ExpoConfig>),
  ios: {
    bundleIdentifier: "com.anuragparida.atlas.chat",
    supportsTablet: false,
    infoPlist: {
      NSAppTransportSecurity: {
        NSAllowsArbitraryLoads: true,
        NSAllowsLocalNetworking: true,
      },
    },
  },
  android: {
    package: "com.anuragparida.atlas.chat",
  },
  plugins: ["expo-router"],
  experiments: {
    typedRoutes: true,
  },
  extra: {
    router: {
      origin: false,
    },
  },
});

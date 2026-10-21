import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.example.cointrail",
  appName: "Cointrail",
  webDir: "mobile/webroot",
  server: {
    url: "https://cointrail.example.com",
    cleartext: false,
    androidScheme: "https",
    errorPath: "offline.html",
  },
  android: {
    allowMixedContent: false,
    captureInput: true,
    webContentsDebuggingEnabled: false,
  },
};

export default config;

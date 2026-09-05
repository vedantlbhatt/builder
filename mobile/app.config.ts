import type { ExpoConfig } from 'expo/config';

/**
 * Managed workflow, no bare eject.
 *
 * Every reason to eject here is an extension — widgets, Live Activities, a notification
 * service extension — and all of them are deliberately out of scope for v1. They are the
 * largest entitlements surface in the project and none of them moves whether a person
 * shares a card.
 */
const config: ExpoConfig = {
  name: 'Builder',
  slug: 'builder',
  scheme: 'builder',
  version: '0.1.0',
  orientation: 'portrait',
  userInterfaceStyle: 'dark',
  newArchEnabled: true,

  ios: {
    // Grouped under the primary App ID in the Sign in with Apple pane, or Apple scopes
    // the `sub` claim per App ID and the same person gets two accounts.
    bundleIdentifier: 'com.vedantlbhatt.Builder',
    supportsTablet: false,
    infoPlist: {
      ITSAppUsesNonExemptEncryption: false,
      NSCameraUsageDescription:
        'Builder uses the camera only to scan the pairing code shown by the Mac agent.',
    },
  },

  plugins: [
    'expo-router',
    'expo-secure-store',
    'expo-apple-authentication',
    ['expo-camera', { cameraPermission: 'Scan the pairing code shown by your Mac.' }],
    ['expo-notifications', { color: '#FFB300' }],
    'expo-sqlite',
    // Local plugin. Survives `expo prebuild`, which regenerates ios/Podfile.
    './plugins/withFmtConstevalPatch',
  ],

  extra: {
    apiBaseUrl: process.env.BUILDER_API_URL ?? 'http://localhost:8000',
    // Google sign-in runs the browser-based id_token flow with no native SDK. Empty means
    // "not configured": the button renders disabled and says so rather than opening a
    // consent page that would 400.
    googleClientId: process.env.GOOGLE_CLIENT_ID ?? '',
    googleRedirectUri: process.env.GOOGLE_REDIRECT_URI ?? 'builder://auth/google',
    eas: { projectId: process.env.EAS_PROJECT_ID ?? '' },
  },

  experiments: { typedRoutes: true },
};

export default config;

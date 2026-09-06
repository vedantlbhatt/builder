import type { ExpoConfig } from 'expo/config';

import { resolveApiBaseUrl } from './src/config/apiBaseUrl.ts';

/**
 * Managed workflow, no bare eject.
 *
 * Every reason to eject here is an extension — widgets, Live Activities, a notification
 * service extension — and all of them are deliberately out of scope for v1. They are the
 * largest entitlements surface in the project and none of them moves whether a person
 * shares a card.
 */
// The rule and its message live in src/config/apiBaseUrl.ts so `bun test` can run
// them; a guard nobody has ever executed is a guard nobody should trust.
const apiBaseUrl = resolveApiBaseUrl(process.env);

const config: ExpoConfig = {
  name: 'Builder',
  slug: 'builder',
  scheme: 'builder',
  version: '0.1.0',
  orientation: 'portrait',
  userInterfaceStyle: 'dark',
  newArchEnabled: true,

  // Bit's resting pose, rendered from the same sixteen strings the app animates and the
  // same `design/tokens.json` amber. `make icons` re-renders all four from
  // `scripts/gen_app_icons.py`, so the store icon cannot drift from the mascot on screen.
  icon: './assets/icon.png',
  splash: {
    image: './assets/splash-icon.png',
    resizeMode: 'contain',
    backgroundColor: '#141210',
  },

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

  android: {
    package: 'com.vedantlbhatt.Builder',
    adaptiveIcon: {
      // Transparent foreground on the app's own background. Android masks this to the
      // launcher's shape inside the middle 66% of the canvas, which is why the generator
      // draws the character smaller here than in the iOS icon.
      foregroundImage: './assets/adaptive-icon.png',
      backgroundColor: '#141210',
    },
  },

  web: {
    favicon: './assets/favicon.png',
  },

  plugins: [
    'expo-router',
    'expo-secure-store',
    'expo-apple-authentication',
    ['expo-camera', { cameraPermission: 'Scan the pairing code shown by your Mac.' }],
    ['expo-notifications', { color: '#FFB300' }],
    'expo-sqlite',
    // Post media. The picker plugin would also stamp a generic microphone string; expo-av
    // runs after it and its explicit string wins (an explicit value beats an existing one
    // in `IOSConfig.Permissions.applyPermissions`).
    [
      'expo-image-picker',
      { photosPermission: 'Builder attaches the photos you choose to the sessions you share.' },
    ],
    ['expo-av', { microphonePermission: 'Builder records the voice note you add to a shared session.' }],
    // Local plugin. Survives `expo prebuild`, which regenerates ios/Podfile.
    './plugins/withFmtConstevalPatch',
  ],

  extra: {
    apiBaseUrl,
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

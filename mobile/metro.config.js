// Metro configuration. Identical to Expo's default for iOS and Android.
//
// The only addition is a resolver hook that is a no-op unless `platform === 'web'`: on web it
// swaps two modules that have no browser implementation for the shims in `src/web/`.
// Native bundles never see this branch, so their module graph is byte-identical to a project
// with no metro.config.js at all.
//
//   expo-sqlite        `ExpoSQLite` needs the wa-sqlite worker plus cross-origin isolation
//                      headers on web. `src/data/cache.ts` runs a dozen statement shapes over
//                      three tables; `src/web/expoSqlite.web.ts` executes exactly that subset
//                      in memory and throws on anything else, so a new statement fails loudly
//                      (the cache's `guarded` logs it once) rather than returning plausible
//                      empty rows.
//   expo-secure-store  `ExpoSecureStore.web.js` is `export default {}` — every call throws.
//                      `src/web/secureStore.web.ts` keeps the same three async functions over
//                      localStorage, which is also where a browser has nothing better.
const path = require('path');
const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);

const WEB_SHIMS = {
  'expo-sqlite': path.resolve(__dirname, 'src/web/expoSqlite.web.ts'),
  'expo-secure-store': path.resolve(__dirname, 'src/web/secureStore.web.ts'),
};

const defaultResolveRequest = config.resolver.resolveRequest;
config.resolver.resolveRequest = (context, moduleName, platform) => {
  if (platform === 'web' && Object.prototype.hasOwnProperty.call(WEB_SHIMS, moduleName)) {
    return { type: 'sourceFile', filePath: WEB_SHIMS[moduleName] };
  }
  return (defaultResolveRequest ?? context.resolveRequest)(context, moduleName, platform);
};

module.exports = config;

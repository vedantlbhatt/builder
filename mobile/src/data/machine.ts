import * as Application from 'expo-application';
import { Platform } from 'react-native';

/**
 * The per-install identifier the server hashes into a device row.
 *
 * Not the IDFV itself as an identity — it is stable per vendor, so treating it as one
 * would let two apps from the same vendor correlate installs. On Android the equivalent
 * is the SSAID, which is per app-signing-key and per user, so the same reasoning holds.
 *
 * The server's `machine_id` domain is sha256hex, ^[0-9a-f]{64}$, but it treats the value
 * as an opaque id — any 64 lowercase hex chars are accepted. Both identifiers are 32 hex
 * digits or fewer, so the value is lowercased, de-dashed and left-padded with zeros rather
 * than hashed, which keeps expo-crypto out of the dependency list.
 */
export async function getMachineId(): Promise<string> {
  let raw: string | null = null;
  try {
    if (Platform.OS === 'android') {
      raw = Application.getAndroidId ? Application.getAndroidId() : null;
    } else {
      raw = Application.getIosIdForVendorAsync ? await Application.getIosIdForVendorAsync() : null;
    }
  } catch {
    raw = null;
  }
  return normalizeMachineId(raw ?? '');
}

/** Pure part, exported for tests: 64 lowercase hex chars from whatever the platform gave. */
export function normalizeMachineId(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/[^0-9a-f]/g, '')
    .slice(-64)
    .padStart(64, '0');
}

import * as Sharing from 'expo-sharing';
import { Platform } from 'react-native';
import { captureRef } from 'react-native-view-shot';

import { duration } from '../theme';
import { headline, type CardModel } from './RecapCard';

/**
 * Turn the rendered card into a PNG and hand it to the share sheet.
 *
 * This is the strongest reason the phone app exists at all. Sharing happens on the phone;
 * the Mac agent can put an image on the pasteboard, but nobody composes a post there.
 */
export async function shareCard(
  viewRef: React.RefObject<unknown>,
  model: CardModel
): Promise<{ shared: boolean; uri?: string }> {
  if (!viewRef.current) return { shared: false };

  // pixelRatio 2, always. Every timeline downscales what you upload, and a 1x capture of
  // a 1600pt card arrives soft — soft is indistinguishable from cheap.
  const uri = await captureRef(viewRef as never, {
    format: 'png',
    quality: 1,
    result: 'tmpfile',
    // `width`/`height` here are points; the ratio is what controls output resolution.
    ...(Platform.OS === 'ios' ? { pixelRatio: 2 } : {}),
  });

  if (!(await Sharing.isAvailableAsync())) {
    return { shared: false, uri };
  }

  // ONE image, and the caption via the dialog title. X's share extension reliably accepts
  // a single image plus text; a heterogeneous payload gets partially dropped in ways the
  // app cannot detect, so the user discovers the missing half only after posting.
  await Sharing.shareAsync(uri, {
    mimeType: 'image/png',
    UTI: 'public.png',
    dialogTitle: caption(model),
  });

  return { shared: true, uri };
}

/**
 * Caption text.
 *
 * Deliberately plain and short. Anything that reads as generated copy gets deleted before
 * posting, which defeats the point of offering it at all.
 */
export function caption(model: CardModel): string {
  const parts = [headline(model)];
  if (model.repoName) parts.push(`· ${model.repoName}`);
  return parts.join(' ');
}

export function summaryLine(model: CardModel): string {
  return `${duration(model.activeSeconds)} active of ${duration(model.wallSeconds)} elapsed`;
}

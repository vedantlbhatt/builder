import React from 'react';
import { Text, View } from 'react-native';

import { Bar, Chip } from '../analysis/AnalysisView';
import type { BuilderProfile } from '../data/api';
import { PixelBadge } from '../pixel/PixelBadge';
import { colors, space } from '../theme';
import {
  BUILDER_PROFILE_PENDING,
  archetypeLine,
  builderProfileFooter,
  dimensionRows,
  meanLabel,
  topPatterns,
  topTags,
  trendLabel,
} from './builderProfile';

/**
 * "How you build": the person read across their analysed sessions.
 *
 * Every number is the server's aggregate, shown with the count it stands on. `null`
 * means fewer than three analysed sessions in the window, and the card says so through
 * Bit rather than drawing five empty bars that would read as five zeros.
 */

const c = colors('dark');

export function BuilderProfileCard({ profile }: { profile: BuilderProfile | null }) {
  if (!profile) {
    return <PixelBadge state="thinking" text={BUILDER_PROFILE_PENDING} style={{ padding: 0 }} />;
  }

  const rows = dimensionRows(profile);
  const archetype = archetypeLine(profile);
  const tags = topTags(profile);
  const patterns = topPatterns(profile);

  return (
    <>
      {rows.map((d) => {
        const trend = trendLabel(d.trend);
        return (
          <View key={d.dimension} style={{ paddingVertical: 6 }}>
            <View style={{ flexDirection: 'row', alignItems: 'baseline', marginBottom: 4 }}>
              <Text style={{ color: c.text, fontSize: 14, flex: 1 }}>{d.label}</Text>
              {trend ? (
                <Text style={{ color: c.textDim, fontSize: 12, marginRight: space.sm, fontVariant: ['tabular-nums'] }}>
                  {trend}
                </Text>
              ) : null}
              <Text style={{ color: c.text, fontSize: 14, fontWeight: '600', fontVariant: ['tabular-nums'] }}>
                {meanLabel(d.mean)}
              </Text>
            </View>
            <Bar value={d.mean} />
          </View>
        );
      })}

      {archetype ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: space.md }}>
          <Text style={{ color: c.textDim, fontSize: 14, flex: 1 }}>Archetype</Text>
          <Text style={{ color: c.text, fontSize: 14, fontWeight: '600' }}>{archetype}</Text>
        </View>
      ) : null}

      {tags.length > 0 && (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, marginTop: space.md }}>
          {tags.map((t) => (
            <Chip key={t.tag} label={`${t.tag} · ${t.sessions}`} />
          ))}
        </View>
      )}

      {patterns.length > 0 && (
        <View style={{ marginTop: space.md }}>
          {patterns.map((p) => (
            <View key={p.pattern} style={{ paddingVertical: 4 }}>
              <Text style={{ color: c.text, fontSize: 14, fontWeight: '600' }}>
                {p.pattern}
                <Text style={{ color: c.textDim, fontWeight: '400' }}>
                  {' '}
                  · {p.sessions} session{p.sessions === 1 ? '' : 's'}
                </Text>
              </Text>
              {p.example ? (
                <Text style={{ color: c.textDim, fontSize: 13, fontStyle: 'italic', lineHeight: 18 }} numberOfLines={2}>
                  “{p.example}”
                </Text>
              ) : null}
            </View>
          ))}
        </View>
      )}

      <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.md }}>
        {builderProfileFooter(profile)}
      </Text>
    </>
  );
}

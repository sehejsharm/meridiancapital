import React from 'react';
import Svg, { Circle, Defs, G, LinearGradient, Line, Path, Stop } from 'react-native-svg';

/** The compass mark. `detail` drops the fine rings at small sizes. */
export function Mark({ size = 96, detail = true }: { size?: number; detail?: boolean }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 512 512">
      <Defs>
        <LinearGradient id="mAu" x1="0" y1="0" x2="1" y2="1">
          <Stop offset="0%" stopColor="#F2DA92" />
          <Stop offset="38%" stopColor="#D4AF37" />
          <Stop offset="72%" stopColor="#B8912B" />
          <Stop offset="100%" stopColor="#8B6914" />
        </LinearGradient>
        <LinearGradient id="mAuV" x1="0.5" y1="0" x2="0.5" y2="1">
          <Stop offset="0%" stopColor="#F2DA92" />
          <Stop offset="50%" stopColor="#D4AF37" />
          <Stop offset="100%" stopColor="#9A7620" />
        </LinearGradient>
      </Defs>

      <Circle cx="256" cy="256" r="228" fill="none" stroke="url(#mAu)"
              strokeWidth={detail ? 5 : 13} />

      {detail && (
        <G>
          <Circle cx="256" cy="256" r="203" fill="none" stroke="url(#mAu)"
                  strokeWidth={1.6} strokeDasharray="7 13" opacity={0.75} />
          <Circle cx="256" cy="256" r="167" fill="none" stroke="url(#mAu)"
                  strokeWidth={2} opacity={0.9} />
          <Circle cx="256" cy="256" r="132" fill="none" stroke="url(#mAu)"
                  strokeWidth={1.7} opacity={0.72} />
          <Circle cx="256" cy="256" r="99" fill="none" stroke="url(#mAu)"
                  strokeWidth={1.5} opacity={0.55} />
          <Circle cx="256" cy="256" r="68" fill="none" stroke="url(#mAu)"
                  strokeWidth={1.3} opacity={0.4} />
          <G stroke="url(#mAu)" strokeWidth={7} strokeLinecap="round" opacity={0.95}>
            <Line x1="139" y1="139" x2="212" y2="212" />
            <Line x1="373" y1="139" x2="300" y2="212" />
            <Line x1="139" y1="373" x2="212" y2="300" />
            <Line x1="373" y1="373" x2="300" y2="300" />
          </G>
        </G>
      )}

      <Path d="M256 36 L272 232 L256 256 L240 232 Z" fill="url(#mAuV)" />
      <Path d="M256 476 L272 280 L256 256 L240 280 Z" fill="url(#mAuV)" />
      <Path d="M36 256 L232 240 L256 256 L232 272 Z" fill="url(#mAu)" />
      <Path d="M476 256 L280 240 L256 256 L280 272 Z" fill="url(#mAu)" />

      <Circle cx="256" cy="256" r={detail ? 30 : 36} fill="#050506"
              stroke="url(#mAu)" strokeWidth={detail ? 6 : 13} />
      {detail && <Circle cx="256" cy="256" r={12} fill="url(#mAu)" />}
    </Svg>
  );
}

import * as React from 'react';
import { Box, Text } from '../../ink.js';

export type ClawdPose =
  | 'default' // resting pose
  | 'arms-up' // used by the click animation
  | 'look-left'
  | 'look-right';

type Props = {
  pose?: ClawdPose;
};

// Stock-themed replacement for the original pixel mascot: a small candlestick
// chart with alternating bullish (green) and bearish (red) candles. The
// component keeps the original 9x3 footprint and pose prop so the welcome
// layout and click animations keep working unchanged.
const CANDLE_ROWS = [
  ' ▁▃▅▇█▅▃ ',
  ' █▓█▓█▓█ ',
  ' ▔▔▔▔▔▔▔ ',
];

export function Clawd(_props: Props = {}) {
  return (
    <Box flexDirection="column">
      {CANDLE_ROWS.map((row, rowIndex) => (
        <Text
          key={rowIndex}
          backgroundColor={rowIndex === 1 ? 'clawd_background' : undefined}
        >
          {[...row].map((char, index) => (
            <Text
              key={index}
              color={index % 2 === 1 ? 'clawd_bullish' : 'clawd_bearish'}
            >
              {char}
            </Text>
          ))}
        </Text>
      ))}
    </Box>
  );
}

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
// chart. The component keeps the original 9x3 footprint and pose prop so the
// welcome layout and click animations keep working unchanged.
const CANDLES = [
  ' ▁▃▅▇█▅▃ ',
  ' █▓█▓█▓█ ',
  ' ▔▔▔▔▔▔▔ ',
];

export function Clawd(_props: Props = {}) {
  return (
    <Box flexDirection="column">
      <Text color="clawd_body">{CANDLES[0]}</Text>
      <Text color="clawd_body" backgroundColor="clawd_background">
        {CANDLES[1]}
      </Text>
      <Text color="clawd_body">{CANDLES[2]}</Text>
    </Box>
  );
}

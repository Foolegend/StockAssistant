/**
 * Replacement for the private `audio-capture-napi` package.
 * Voice input is unavailable in the reconstruction.
 */
export function isNativeAudioAvailable(): boolean {
  return false
}

export function stopNativeRecording(): void {}

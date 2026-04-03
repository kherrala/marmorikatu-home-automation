export function cryptoRand(): number {
  return crypto.getRandomValues(new Uint32Array(1))[0]! / 4294967296;
}

export function pick<T>(arr: readonly T[]): T {
  return arr[Math.floor(cryptoRand() * arr.length)]!;
}

export function randInt(min: number, max: number): number {
  return Math.floor(cryptoRand() * (max - min + 1)) + min;
}

export function stripThinkTags(text: string): string {
  return text
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/<\/?think\s*\/?>/gi, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/__(.+?)__/g, '$1')
    .replace(/_(.+?)_/g, '$1')
    .replace(/~~(.+?)~~/g, '$1')
    .replace(/`(.+?)`/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .trim();
}

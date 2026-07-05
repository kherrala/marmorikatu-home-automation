import { ttsAudio } from '../dom/elements.js';
import { setSpeaking } from '../dom/avatar.js';
import { resumeIfSuspended } from './context.js';
import { showAudioHint, hideAudioHint } from './audio-unlock.js';

const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

/** PCM WAV duration in ms from header byte rate; null if not parseable. */
function wavDurationMs(arr: Uint8Array): number | null {
  if (arr.length < 44 || arr[0] !== 0x52 /* R */ || arr[8] !== 0x57 /* W */) return null;
  const byteRate = arr[28]! | (arr[29]! << 8) | (arr[30]! << 16) | (arr[31]! << 24);
  if (byteRate <= 0) return null;
  return ((arr.length - 44) / byteRate) * 1000;
}

export function playSentence(b64wav: string): Promise<boolean> {
  return new Promise(resolve => {
    const bytes = atob(b64wav);
    const arr = new Uint8Array(bytes.length);
    for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
    const url = URL.createObjectURL(new Blob([arr], { type: 'audio/wav' }));
    let safetyTimer: ReturnType<typeof setTimeout>;
    const cleanup = () => {
      clearTimeout(safetyTimer);
      ttsAudio.onended = null;
      ttsAudio.onerror = null;
      URL.revokeObjectURL(url);
    };
    ttsAudio.src = url;
    ttsAudio.currentTime = 0;
    setSpeaking(true);
    // Safety timeout scales with the clip: a fixed cap resolved long clips
    // (news headline runs) mid-playback, so the caller re-opened the mic
    // while the speaker was still talking and the avatar prompted itself.
    // If it ever fires, stop the runaway audio before resolving.
    const durMs = wavDurationMs(arr) ?? 15_000;
    safetyTimer = setTimeout(() => {
      try { ttsAudio.pause(); } catch {}
      cleanup();
      resolve(false);
    }, durMs + 5_000);
    ttsAudio.onended = () => { cleanup(); resolve(true); };
    ttsAudio.onerror = () => { cleanup(); resolve(false); };
    // Await the AudioContext resume BEFORE play(): the element is routed
    // through the context, and until resume completes it plays silently —
    // audibly cutting the start of the clip.
    // A rejected play() is iOS's autoplay block — audio re-locked since the
    // last tap. Surface the hint so a single tap re-arms it.
    resumeIfSuspended()
      .then(() => ttsAudio.play())
      .then(() => hideAudioHint())
      .catch(() => { showAudioHint(); cleanup(); resolve(false); });
  });
}

export async function speakAndWait(
  text: string,
  onSentence?: (sentence: string) => void,
): Promise<void> {
  // Try server-side TTS first -- streams NDJSON, one sentence per line
  try {
    const res = await fetch('/api/chat/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (res.ok) {
      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let played = 0;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop()!;
        for (const line of lines) {
          if (!line.trim()) continue;
          const parsed = JSON.parse(line) as { audio: string; text?: string };
          if (parsed.text) onSentence?.(parsed.text);
          await playSentence(parsed.audio);
          played++;
        }
      }
      if (buf.trim()) {
        try {
          const parsed = JSON.parse(buf) as { audio: string; text?: string };
          if (parsed.text) onSentence?.(parsed.text);
          await playSentence(parsed.audio);
          played++;
        } catch {}
      }
      setSpeaking(false);
      if (played > 0) return;
    }
  } catch { /* server TTS failed, fall back to browser */ }

  // Fallback: browser speechSynthesis
  speechSynthesis.cancel();
  return new Promise(resolve => {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'fi-FI';
    const voices = speechSynthesis.getVoices();
    const fiFemale = voices.find(v => v.lang.startsWith('fi') && /female|satu|marjut/i.test(v.name));
    const fiVoice = fiFemale || voices.find(v => v.lang.startsWith('fi'));
    if (fiVoice) utterance.voice = fiVoice;
    utterance.rate = 1.0;
    utterance.onstart = () => {
      setSpeaking(true);
    };
    utterance.onend = () => { setSpeaking(false); resolve(); };
    utterance.onerror = () => { setSpeaking(false); resolve(); };
    speechSynthesis.speak(utterance);
    if (isIOS) { speechSynthesis.pause(); speechSynthesis.resume(); }
  });
}

export function isSpeaking(): boolean {
  return speechSynthesis.speaking || !ttsAudio.paused;
}

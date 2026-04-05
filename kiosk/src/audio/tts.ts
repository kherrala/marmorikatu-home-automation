import { ttsAudio } from '../dom/elements.js';
import { setSpeaking } from '../dom/avatar.js';
import { resumeIfSuspended } from './context.js';

const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

export function playSentence(b64wav: string): Promise<boolean> {
  return new Promise(resolve => {
    resumeIfSuspended();
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
    ttsAudio.play().catch(() => { cleanup(); resolve(false); });
    setSpeaking(true);
    safetyTimer = setTimeout(() => { cleanup(); resolve(false); }, 15_000);
    ttsAudio.onended = () => { cleanup(); resolve(true); };
    ttsAudio.onerror = () => { cleanup(); resolve(false); };
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

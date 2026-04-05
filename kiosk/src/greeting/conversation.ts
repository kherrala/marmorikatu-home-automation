import { dispatch, getState } from '../state/store.js';
import { stripThinkTags } from '../content/text-utils.js';
import { randomFallback } from '../content/fallbacks.js';
import { pick } from '../content/text-utils.js';
import { speakAndWait, playSentence } from '../audio/tts.js';
import { setSpeaking } from '../dom/avatar.js';
import { reportText, reportSpinner, userTextEl } from '../dom/elements.js';
import { KioskPhase } from '../types/state.js';
import { captureFrame, isVisionRequest } from '../camera/capture.js';

// Only match short farewell-only utterances (max ~30 chars).
const FAREWELL_PATTERNS = /^(heippa|heihei|hei\s*hei|näkemiin|nähdään|moi\s*moi|moikka|kiitos|bye|goodbye|see\s*you)[.!]?\s*$/i;

export function isFarewell(text: string): boolean {
  return FAREWELL_PATTERNS.test(text);
}

/** Stream AI response with inline TTS — plays first sentence while LLM still generates. */
async function streamChatWithTTS(
  onSentence: (text: string) => void,
  onToolUse?: (toolName: string) => void,
): Promise<{ response: string; toolCalls: Array<{ tool: string }> } | null> {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 180000);
    const { greeting } = getState();
    const res = await fetch('/api/chat/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: greeting.conversationHistory }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    if (!res.ok) return null;

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let fullResponse = '';
    let toolCalls: Array<{ tool: string }> = [];

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop()!;

      for (const line of lines) {
        // SSE format: "data: {...}" — extract JSON after "data: " prefix
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;
        const jsonStr = trimmed.slice(6); // strip "data: "
        if (!jsonStr) continue;
        const parsed = JSON.parse(jsonStr) as {
          audio?: string;
          text?: string;
          done?: boolean;
          response?: string;
          tool_calls?: Array<{ tool: string }>;
          tool_use?: string;
        };

        if (parsed.tool_use) {
          onToolUse?.(parsed.tool_use);
          continue;
        }

        if (parsed.done) {
          fullResponse = parsed.response ?? fullResponse;
          toolCalls = parsed.tool_calls ?? toolCalls;
          // Don't call setSpeaking(false) here — the last playSentence
          // might still be playing. It clears itself when onended fires.
          continue;
        }

        if (parsed.text) {
          onSentence(parsed.text);
        }
        if (parsed.audio) {
          await playSentence(parsed.audio);
          // Brief pause between sentences for natural rhythm
          await new Promise(r => setTimeout(r, 50));
        }
      }
    }

    // Flush remaining SSE data
    const remaining = buf.trim().startsWith('data: ') ? buf.trim().slice(6) : buf.trim();
    if (remaining) {
      try {
        const parsed = JSON.parse(remaining) as { done?: boolean; response?: string; audio?: string; text?: string; tool_calls?: Array<{ tool: string }> };
        if (parsed.done) {
          fullResponse = parsed.response ?? fullResponse;
          toolCalls = parsed.tool_calls ?? toolCalls;
        } else if (parsed.audio) {
          if (parsed.text) onSentence(parsed.text);
          await playSentence(parsed.audio);
        }
      } catch { /* incomplete line */ }
    }

    setSpeaking(false);
    // Brief pause to ensure audio playback is fully complete before mic opens
    await new Promise(r => setTimeout(r, 200));
    return { response: stripThinkTags(fullResponse), toolCalls };
  } catch {
    return null;
  }
}

/** Non-streaming chat (used by daily report + fallback). */
export async function generateAIResponse(): Promise<string | null> {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 90000);
    const { greeting } = getState();
    const res = await fetch('/api/chat/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: greeting.conversationHistory }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    if (res.ok) {
      const data = await res.json() as { response?: string };
      const text = data.response?.trim();
      if (text) return stripThinkTags(text);
    }
  } catch { /* Bridge unavailable */ }
  return null;
}


let startListeningFn: (() => void) | null = null;
let dismissGreetingFn: (() => void) | null = null;
let pauseListeningFn: (() => void) | null = null;

export function setConversationHandlers(handlers: {
  startListening: () => void;
  dismissGreeting: () => void;
  pauseListening: () => void;
}): void {
  startListeningFn = handlers.startListening;
  dismissGreetingFn = handlers.dismissGreeting;
  pauseListeningFn = handlers.pauseListening;
}

export async function handleVoiceResult(transcript: string): Promise<void> {
  userTextEl.textContent = `"${transcript}"`;
  dispatch({ type: 'SET_PROCESSING', processing: true });

  pauseListeningFn?.();

  try {
    // Farewell detection
    if (isFarewell(transcript)) {
      const goodbye = pick(['Heippa!', 'Nähdään!', 'Moikka!', 'Hei hei!']);
      reportSpinner.classList.add('hidden');
      reportText.textContent = goodbye;
      await speakAndWait(goodbye);
      dismissGreetingFn?.();
      return;
    }

    // Capture camera frame if user asks for vision analysis
    const visionFrame = isVisionRequest(transcript) ? captureFrame() : null;
    const message = visionFrame
      ? { role: 'user' as const, content: transcript, images: [visionFrame] }
      : { role: 'user' as const, content: transcript };
    dispatch({ type: 'CONVERSATION_ADD', message });
    reportSpinner.classList.remove('hidden');

    // Try streaming (plays first sentence while LLM generates the rest)
    const streamResult = await streamChatWithTTS(
      (sentence) => {
        reportSpinner.classList.add('hidden');
        userTextEl.textContent = '';
        reportText.textContent = sentence;
      },
      (toolName) => {
        // Show which tool is being used in the main text area
        reportSpinner.classList.add('hidden');
        userTextEl.textContent = '';
        reportText.textContent = `🔧 ${toolName}`;
      },
    );

    if (streamResult) {
      reportSpinner.classList.add('hidden');
      dispatch({ type: 'CONVERSATION_ADD', message: { role: 'assistant', content: streamResult.response } });
      dispatch({ type: 'SET_HAD_VOICE_INPUT' });
    } else {
      // Fallback: non-streaming
      const response = await generateAIResponse() || randomFallback();
      reportSpinner.classList.add('hidden');
      userTextEl.textContent = '';
      dispatch({ type: 'CONVERSATION_ADD', message: { role: 'assistant', content: response } });
      dispatch({ type: 'SET_HAD_VOICE_INPUT' });
      await speakAndWait(response, (sentence) => {
        reportText.textContent = sentence;
      });
    }

    if (getState().phase === KioskPhase.GREETING) {
      startListeningFn?.();
    }
  } finally {
    dispatch({ type: 'SET_PROCESSING', processing: false });
  }
}

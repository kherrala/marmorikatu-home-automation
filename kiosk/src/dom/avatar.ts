import { avatarEl } from './elements.js';

export function setSpeaking(speaking: boolean): void {
  if (speaking) {
    avatarEl.classList.add('speaking');
    avatarEl.classList.remove('listening');
  } else {
    avatarEl.classList.remove('speaking');
  }
}

export function setListening(listening: boolean): void {
  if (listening) {
    avatarEl.classList.add('listening');
    avatarEl.classList.remove('speaking');
  } else {
    avatarEl.classList.remove('listening');
  }
}

export function clearAvatar(): void {
  avatarEl.classList.remove('speaking', 'listening');
}

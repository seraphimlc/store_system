export interface InstallPrompt extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}
export function standalone(): boolean {
  return matchMedia('(display-mode: standalone)').matches || Boolean((navigator as Navigator & { standalone?: boolean }).standalone);
}
export function iosDevice(): boolean {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

/** No global controllerchange reload: only this user's explicit confirmation reloads this tab. */
export function activateUpdate(worker: ServiceWorker): Promise<void> {
  return new Promise((resolve, reject) => {
    const finish = (error?: Error) => {
      clearTimeout(timeout);
      worker.removeEventListener('statechange', changed);
      if (error) reject(error); else resolve();
    };
    const changed = () => {
      if (worker.state === 'activated') finish();
      else if (worker.state === 'redundant') finish(new Error('Update superseded'));
    };
    const timeout = setTimeout(() => finish(new Error('Update timed out')), 15000);
    worker.addEventListener('statechange', changed);
    if (worker.state !== 'installed') { finish(new Error('Update no longer waiting')); return; }
    try { worker.postMessage({ type: 'STAFF_PWA_ACTIVATE' }); } catch (error) { finish(error instanceof Error ? error : new Error('Update failed')); }
  });
}

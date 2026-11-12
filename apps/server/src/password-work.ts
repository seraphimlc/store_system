import { BusinessError } from './errors';
/** Bound Argon2 memory use even when many staff sign in at once. */
export class PasswordWork {
  private active = 0;
  private readonly waiting: Array<() => void> = [];
  constructor(private readonly concurrency = 2, private readonly maxWaiting = 32) {}
  async run<T>(work: () => Promise<T>): Promise<T> {
    if (this.active >= this.concurrency) {
      if (this.waiting.length >= this.maxWaiting) throw new BusinessError('AUTH_BUSY', '登录请求较多，请稍后重试', 429);
      await new Promise<void>(resolve => this.waiting.push(resolve));
    } else this.active++;
    try { return await work(); }
    finally { const next = this.waiting.shift(); if (next) next(); else this.active--; }
  }
}
export const passwordWork = new PasswordWork();

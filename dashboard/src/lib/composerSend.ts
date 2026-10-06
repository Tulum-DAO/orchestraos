/**
 * Send-timeout semantics for the composer.
 *
 * A HUNG API never rejects on its own: the socket stays open and the promise never settles,
 * so the composer spins forever and the operator learns nothing. A 15s race fixes that.
 *
 * But a race only settles OUR promise. It does NOT abort the request, and even aborting the
 * client would not un-send something the server already processed. So a timeout is an
 * UNKNOWN outcome, not a known failure — and reporting it as "nothing was sent" is the lie
 * that causes the duplicate: the operator believes the send failed, sends again, and the
 * agent receives the instruction twice. Duplicate delivery is the one failure this surface
 * exists to make legible, so it must not manufacture one.
 */

/** Distinguishes "we gave up waiting" from "the connection actually failed". */
export class SendTimeout extends Error {
  constructor(ms: number) {
    super(`no answer after ${ms}ms`);
    this.name = 'SendTimeout';
  }
}

export const SEND_TIMEOUT_MS = 15_000;

/**
 * Reject with SendTimeout after `ms`, and ALWAYS clear the timer.
 *
 * The previous version leaked its `setTimeout` on the success path: every send left a timer
 * armed for 15s that then rejected an already-settled promise.
 */
export function withTimeout<T>(
  pr: Promise<T>,
  ms: number = SEND_TIMEOUT_MS,
  setTimer: typeof setTimeout = setTimeout,
  clearTimer: typeof clearTimeout = clearTimeout,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const id = setTimer(() => reject(new SendTimeout(ms)), ms);
    const done = () => clearTimer(id);
    pr.then(
      (v) => { done(); resolve(v); },
      (e) => { done(); reject(e); },
    );
  });
}

/**
 * What to tell the operator when a send throws.
 *
 * The two cases differ in what the operator should DO, which is the only reason to
 * distinguish them: a refused connection is safe to retry, an unanswered one is not.
 */
export function sendFailureNote(err: unknown): string {
  if (err instanceof SendTimeout) {
    return 'No answer from the server in time. Your message may ALREADY have been delivered — '
      + 'check the transcript before sending it again.';
  }
  return "Can't reach the server — nothing was sent. Your message and photo are still here.";
}

/** Whether the composer may safely offer a one-click retry. Never after an unknown outcome. */
export function isSafeToRetry(err: unknown): boolean {
  return !(err instanceof SendTimeout);
}

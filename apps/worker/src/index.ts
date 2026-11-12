/** P1 package boundary only. Durable task claiming/execution is implemented in P2/P5. */
export interface TaskEnvelope { id: string; kind: string; requestId: string; createdAt: string }
export const WORKER_IMPLEMENTED = false;

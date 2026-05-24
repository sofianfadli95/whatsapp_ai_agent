/**
 * In-memory metrics counters for the WhatsApp Gateway.
 * Exposes simple increment/get functions as a singleton module.
 */

export interface MetricsSnapshot {
  inbound_forwarded: number;
  inbound_failed: number;
  outbound_sent: number;
  outbound_failed: number;
  queue_depth: number;
}

const counters: MetricsSnapshot = {
  inbound_forwarded: 0,
  inbound_failed: 0,
  outbound_sent: 0,
  outbound_failed: 0,
  queue_depth: 0,
};

/** Increment the inbound_forwarded counter */
export function incrInboundForwarded(): void {
  counters.inbound_forwarded++;
}

/** Increment the inbound_failed counter */
export function incrInboundFailed(): void {
  counters.inbound_failed++;
}

/** Increment the outbound_sent counter */
export function incrOutboundSent(): void {
  counters.outbound_sent++;
}

/** Increment the outbound_failed counter */
export function incrOutboundFailed(): void {
  counters.outbound_failed++;
}

/** Set the queue_depth gauge to a specific value */
export function setQueueDepth(depth: number): void {
  counters.queue_depth = depth;
}

/** Get a snapshot of all current counter values */
export function getMetrics(): MetricsSnapshot {
  return { ...counters };
}

/** Reset all counters to zero (for testing) */
export function resetMetrics(): void {
  counters.inbound_forwarded = 0;
  counters.inbound_failed = 0;
  counters.outbound_sent = 0;
  counters.outbound_failed = 0;
  counters.queue_depth = 0;
}

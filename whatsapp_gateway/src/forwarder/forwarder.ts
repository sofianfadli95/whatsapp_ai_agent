/**
 * HTTP forwarder with exponential-backoff retry and durable overflow queue.
 * POSTs InboundEvents to the Python backend; on exhausted retries, persists
 * events to an on-disk NDJSON overflow file for later re-delivery.
 */

import axios, { type AxiosInstance } from "axios";
import { mkdir, readFile, writeFile, appendFile, unlink } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import pino from "pino";
import { incrInboundForwarded, incrInboundFailed } from "../observability/metrics.js";

export type { ForwardFn, InboundEvent } from "../baileys/inbound.js";
import type { ForwardFn, InboundEvent } from "../baileys/inbound.js";

// ─── Types ──────────────────────────────────────────────────────────────────────

export interface ForwarderOptions {
  /** URL to POST inbound events to */
  backendUrl: string;
  /** Bearer token for Authorization header */
  token: string;
  /** Directory for the overflow outbox (will be created if missing) */
  outboxDir: string;
  /** Request timeout in ms (default: 10000) */
  timeoutMs?: number;
  /** Max retry attempts (default: 3) */
  maxAttempts?: number;
  /** Base delay in ms for exponential backoff (default: 1000) */
  baseDelayMs?: number;
  /** Max delay cap in ms (default: 8000) */
  maxDelayMs?: number;
  /** Overflow scanner interval in ms (default: 30000) */
  scanIntervalMs?: number;
  /** Optional pino logger instance */
  logger?: pino.Logger;
}

export interface Forwarder {
  /** The forward function to pass to registerInboundHandler */
  forward: ForwardFn;
  /** Start the background overflow scanner */
  startScanner: () => void;
  /** Stop the background overflow scanner */
  stopScanner: () => void;
  /** Manually flush the overflow queue (for testing) */
  flushOverflow: () => Promise<void>;
}

// ─── Helpers ────────────────────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getRetryDelay(attempt: number, baseMs: number, maxMs: number): number {
  // attempt is 0-indexed: delays are baseMs * 2^attempt → 1s, 2s, 4s...
  const delay = baseMs * Math.pow(2, attempt);
  return Math.min(delay, maxMs);
}

// ─── Factory ────────────────────────────────────────────────────────────────────

export function createForwarder(options: ForwarderOptions): Forwarder {
  const {
    backendUrl,
    token,
    outboxDir,
    timeoutMs = 10_000,
    maxAttempts = 3,
    baseDelayMs = 1000,
    maxDelayMs = 8000,
    scanIntervalMs = 30_000,
  } = options;

  const logger = options.logger ?? pino({ name: "forwarder" });

  const client: AxiosInstance = axios.create({
    baseURL: backendUrl,
    timeout: timeoutMs,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });

  const overflowFile = path.join(outboxDir, "overflow.ndjson");

  let scannerTimer: ReturnType<typeof setInterval> | null = null;

  // ─── Ensure outbox directory exists ─────────────────────────────────────────

  async function ensureOutboxDir(): Promise<void> {
    if (!existsSync(outboxDir)) {
      await mkdir(outboxDir, { recursive: true });
    }
  }

  // ─── Post event to backend ──────────────────────────────────────────────────

  async function postEvent(event: InboundEvent): Promise<void> {
    await client.post("", event);
  }

  // ─── Persist event to overflow queue ────────────────────────────────────────

  async function persistToOverflow(event: InboundEvent): Promise<void> {
    await ensureOutboxDir();
    const line = JSON.stringify(event) + "\n";
    await appendFile(overflowFile, line, "utf-8");
    logger.warn(
      { messageId: event.baileys_message_id },
      "Event persisted to overflow queue after exhausted retries",
    );
  }

  // ─── Forward with retries ──────────────────────────────────────────────────

  const forward: ForwardFn = async (event: InboundEvent): Promise<void> => {
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      try {
        await postEvent(event);
        incrInboundForwarded();
        logger.info(
          { messageId: event.baileys_message_id, attempt: attempt + 1 },
          "Event forwarded successfully",
        );
        return;
      } catch (err) {
        const isLastAttempt = attempt === maxAttempts - 1;
        logger.warn(
          { messageId: event.baileys_message_id, attempt: attempt + 1, error: (err as Error).message },
          isLastAttempt ? "Final retry attempt failed" : "Retry attempt failed, will retry",
        );

        if (!isLastAttempt) {
          const delay = getRetryDelay(attempt, baseDelayMs, maxDelayMs);
          await sleep(delay);
        }
      }
    }

    // All retries exhausted — persist to overflow
    incrInboundFailed();
    await persistToOverflow(event);
  };

  // ─── Overflow scanner ───────────────────────────────────────────────────────

  async function flushOverflow(): Promise<void> {
    if (!existsSync(overflowFile)) return;

    let content: string;
    try {
      content = await readFile(overflowFile, "utf-8");
    } catch {
      return; // File may have been removed between check and read
    }

    const lines = content.split("\n").filter((line) => line.trim().length > 0);
    if (lines.length === 0) return;

    logger.info({ count: lines.length }, "Overflow scanner: attempting re-delivery");

    const remaining: string[] = [];

    for (const line of lines) {
      try {
        const event: InboundEvent = JSON.parse(line);
        await postEvent(event);
        logger.info(
          { messageId: event.baileys_message_id },
          "Overflow event re-delivered successfully",
        );
      } catch {
        // Backend still unreachable — keep this event in the queue
        remaining.push(line);
      }
    }

    // Rewrite the file with only the remaining events (or remove if empty)
    if (remaining.length === 0) {
      await unlink(overflowFile);
      logger.info("Overflow queue fully drained");
    } else {
      await writeFile(overflowFile, remaining.join("\n") + "\n", "utf-8");
      logger.warn(
        { remaining: remaining.length },
        "Overflow scanner: some events still pending",
      );
    }
  }

  function startScanner(): void {
    if (scannerTimer) return;
    scannerTimer = setInterval(() => {
      void flushOverflow();
    }, scanIntervalMs);
    logger.info({ intervalMs: scanIntervalMs }, "Overflow scanner started");
  }

  function stopScanner(): void {
    if (scannerTimer) {
      clearInterval(scannerTimer);
      scannerTimer = null;
      logger.info("Overflow scanner stopped");
    }
  }

  return { forward, startScanner, stopScanner, flushOverflow };
}

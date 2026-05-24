import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { bearerAuth } from "../middleware/auth.js";
import type { SessionManagerInterface } from "../baileys/session.js";
import { incrOutboundSent, incrOutboundFailed } from "../observability/metrics.js";

/**
 * Zod schema for the POST /send request body.
 * - `to`: E.164 phone number (starts with +, 8-15 digits)
 * - `body`: non-empty message text
 * - `idempotency_key`: non-empty string for deduplication
 */
const sendBodySchema = z.object({
  to: z
    .string()
    .regex(/^\+\d{8,15}$/, "Must be a valid E.164 phone number"),
  body: z.string().min(1, "Message body must not be empty"),
  idempotency_key: z.string().min(1, "Idempotency key must not be empty"),
});

/**
 * Simple bounded LRU cache with TTL support.
 */
class LRUCache<K, V> {
  private map = new Map<K, { value: V; expiresAt: number }>();
  private maxSize: number;
  private ttlMs: number;

  constructor(maxSize: number, ttlMs: number) {
    this.maxSize = maxSize;
    this.ttlMs = ttlMs;
  }

  get(key: K): V | undefined {
    const entry = this.map.get(key);
    if (!entry) return undefined;

    // Check TTL expiry
    if (Date.now() > entry.expiresAt) {
      this.map.delete(key);
      return undefined;
    }

    // Move to end (most recently used)
    this.map.delete(key);
    this.map.set(key, entry);
    return entry.value;
  }

  set(key: K, value: V): void {
    // If key exists, delete it first to refresh position
    if (this.map.has(key)) {
      this.map.delete(key);
    }

    // Evict oldest entry if at capacity
    if (this.map.size >= this.maxSize) {
      const firstKey = this.map.keys().next().value;
      if (firstKey !== undefined) {
        this.map.delete(firstKey);
      }
    }

    this.map.set(key, { value, expiresAt: Date.now() + this.ttlMs });
  }
}

export interface SendRouteOptions {
  sessionManager: SessionManagerInterface;
}

// Idempotency cache: max 10000 entries, 5-minute TTL
const idempotencyCache = new LRUCache<string, { statusCode: number; body: unknown }>(
  10_000,
  5 * 60 * 1000,
);

/**
 * Converts an E.164 phone number to a Baileys JID.
 * E.g., "+6281234567890" → "6281234567890@s.whatsapp.net"
 */
function e164ToJid(phone: string): string {
  return `${phone.slice(1)}@s.whatsapp.net`;
}

/**
 * Registers the POST /send route.
 *
 * - Validates request body with Zod (E.164 phone, non-empty body, idempotency key).
 * - Returns cached response for duplicate idempotency keys within TTL.
 * - Returns 503 if the Baileys socket is not connected.
 * - Calls sock.sendMessage with a 10s timeout.
 * - Returns 200 on success, 504 on timeout, 502 on Baileys errors.
 */
export async function sendRoute(
  app: FastifyInstance,
  opts: SendRouteOptions,
): Promise<void> {
  const { sessionManager } = opts;

  app.post(
    "/send",
    { preHandler: [bearerAuth] },
    async (request, reply) => {
      // Validate request body
      const parseResult = sendBodySchema.safeParse(request.body);
      if (!parseResult.success) {
        return reply.status(400).send({
          status: "validation_error",
          errors: parseResult.error.flatten().fieldErrors,
        });
      }

      const { to, body, idempotency_key } = parseResult.data;

      // Check idempotency cache
      const cached = idempotencyCache.get(idempotency_key);
      if (cached) {
        return reply.status(cached.statusCode).send(cached.body);
      }

      // Check connection status
      if (!sessionManager.isConnected()) {
        return reply.status(503).send({ status: "not_connected" });
      }

      const sock = sessionManager.getSocket();
      if (!sock) {
        return reply.status(503).send({ status: "not_connected" });
      }

      // Convert E.164 to Baileys JID
      const jid = e164ToJid(to);

      // Send message with 10s timeout
      try {
        const sendPromise = sock.sendMessage(jid, { text: body });

        const timeoutPromise = new Promise<never>((_, reject) => {
          setTimeout(() => reject(new Error("__SEND_TIMEOUT__")), 10_000);
        });

        const result = await Promise.race([sendPromise, timeoutPromise]);

        // Extract message_id from Baileys response
        const messageId =
          result?.key?.id ?? `msg_${Date.now()}`;

        const responseBody = { status: "sent", message_id: messageId };

        // Cache successful response
        idempotencyCache.set(idempotency_key, {
          statusCode: 200,
          body: responseBody,
        });

        incrOutboundSent();
        return reply.status(200).send(responseBody);
      } catch (err: unknown) {
        const error = err instanceof Error ? err : new Error(String(err));

        if (error.message === "__SEND_TIMEOUT__") {
          incrOutboundFailed();
          return reply.status(504).send({ status: "timeout" });
        }

        // Baileys error
        incrOutboundFailed();
        return reply.status(502).send({
          status: "send_failed",
          error_code: error.message || "unknown_error",
        });
      }
    },
  );
}

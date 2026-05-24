/**
 * messages.upsert handler + InboundEvent builder.
 * Subscribes to Baileys socket events, deduplicates, normalizes, and forwards inbound messages.
 */

import { z } from "zod";
import { normalizeToE164 } from "../utils/phone.js";

// ─── InboundEvent Zod Schema ───────────────────────────────────────────────────

export const MessageTypeEnum = z.enum([
  "text",
  "image",
  "audio",
  "video",
  "document",
  "sticker",
  "location",
  "unknown",
]);

export const InboundEventSchema = z.object({
  baileys_message_id: z.string(),
  sender_jid: z.string(),
  sender_phone_e164: z.string(),
  message_type: MessageTypeEnum,
  text_body: z.string().nullable(),
  event_timestamp: z.number(),
  raw_key: z.object({
    remoteJid: z.string(),
    id: z.string(),
    fromMe: z.boolean(),
  }),
});

export type InboundEvent = z.infer<typeof InboundEventSchema>;
export type MessageType = z.infer<typeof MessageTypeEnum>;

/**
 * Forward function signature: accepts a validated InboundEvent and delivers it
 * to the Python backend.
 */
export type ForwardFn = (event: InboundEvent) => Promise<void>;

// ─── Bounded LRU Cache for Deduplication ────────────────────────────────────────

export class BoundedLRU {
  private cache: Map<string, boolean>;
  private maxSize: number;

  constructor(maxSize = 1000) {
    this.maxSize = maxSize;
    this.cache = new Map();
  }

  /**
   * Returns true if the key already exists (duplicate).
   * If new, adds it to the cache and evicts the oldest entry if at capacity.
   */
  has(key: string): boolean {
    if (this.cache.has(key)) {
      // Move to end (most recently used)
      this.cache.delete(key);
      this.cache.set(key, true);
      return true;
    }
    return false;
  }

  /**
   * Adds a key to the cache. Evicts oldest if at capacity.
   */
  add(key: string): void {
    if (this.cache.has(key)) {
      this.cache.delete(key);
    } else if (this.cache.size >= this.maxSize) {
      // Evict the oldest (first) entry
      const firstKey = this.cache.keys().next().value;
      if (firstKey !== undefined) {
        this.cache.delete(firstKey);
      }
    }
    this.cache.set(key, true);
  }

  get size(): number {
    return this.cache.size;
  }
}

// ─── Message Type Classification ────────────────────────────────────────────────

/**
 * Classifies a Baileys message object into a MessageType.
 */
export function classifyMessageType(message: Record<string, unknown>): MessageType {
  if (message.conversation || message.extendedTextMessage) {
    return "text";
  }
  if (message.imageMessage) {
    return "image";
  }
  if (message.audioMessage) {
    return "audio";
  }
  if (message.videoMessage) {
    return "video";
  }
  if (message.documentMessage || message.documentWithCaptionMessage) {
    return "document";
  }
  if (message.stickerMessage) {
    return "sticker";
  }
  if (message.locationMessage || message.liveLocationMessage) {
    return "location";
  }
  return "unknown";
}

/**
 * Extracts the text body from a Baileys message, if available.
 */
export function extractTextBody(message: Record<string, unknown>): string | null {
  if (typeof message.conversation === "string") {
    return message.conversation;
  }
  if (
    message.extendedTextMessage &&
    typeof (message.extendedTextMessage as Record<string, unknown>).text === "string"
  ) {
    return (message.extendedTextMessage as Record<string, unknown>).text as string;
  }
  return null;
}

// ─── Handler Registration ───────────────────────────────────────────────────────

export interface BaileysSocket {
  ev: {
    on(event: string, handler: (...args: unknown[]) => void): void;
  };
}

/**
 * Registers the messages.upsert event handler on a Baileys socket.
 * Filters, deduplicates, normalizes, validates, and forwards inbound messages.
 *
 * @param socket - The Baileys WASocket (or compatible interface)
 * @param forwardFn - Function to call with each validated InboundEvent
 * @param options - Optional configuration (LRU max size)
 * @returns The LRU cache instance (for testing/inspection)
 */
export function registerInboundHandler(
  socket: BaileysSocket,
  forwardFn: ForwardFn,
  options?: { lruMaxSize?: number },
): BoundedLRU {
  const dedupeCache = new BoundedLRU(options?.lruMaxSize ?? 1000);

  socket.ev.on("messages.upsert", (upsert: unknown) => {
    const { messages } = upsert as { messages: Array<Record<string, unknown>> };

    if (!Array.isArray(messages)) {
      return;
    }

    for (const msg of messages) {
      const key = msg.key as
        | { remoteJid?: string; id?: string; fromMe?: boolean }
        | undefined;

      if (!key) continue;

      // Filter out messages sent by us
      if (key.fromMe === true) continue;

      const remoteJid = key.remoteJid;
      const messageId = key.id;

      if (!remoteJid || !messageId) continue;

      // Deduplicate by (remoteJid, id)
      const dedupeKey = `${remoteJid}:${messageId}`;
      if (dedupeCache.has(dedupeKey)) continue;
      dedupeCache.add(dedupeKey);

      // Normalize phone number to E.164
      const phoneE164 = normalizeToE164(remoteJid);
      if (!phoneE164) continue; // Skip group messages or invalid JIDs

      // Classify message type and extract text
      const messageContent = (msg.message ?? {}) as Record<string, unknown>;
      const messageType = classifyMessageType(messageContent);
      const textBody = extractTextBody(messageContent);

      // Build the InboundEvent payload
      const eventPayload = {
        baileys_message_id: messageId,
        sender_jid: remoteJid,
        sender_phone_e164: phoneE164,
        message_type: messageType,
        text_body: textBody,
        event_timestamp: typeof msg.messageTimestamp === "number"
          ? msg.messageTimestamp
          : Math.floor(Date.now() / 1000),
        raw_key: {
          remoteJid,
          id: messageId,
          fromMe: false,
        },
      };

      // Validate with Zod
      const parsed = InboundEventSchema.safeParse(eventPayload);
      if (!parsed.success) continue;

      // Forward the validated event (fire-and-forget from the handler's perspective)
      void forwardFn(parsed.data);
    }
  });

  return dedupeCache;
}

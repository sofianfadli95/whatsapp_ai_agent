import { describe, it, expect, vi } from "vitest";
import {
  registerInboundHandler,
  BoundedLRU,
  classifyMessageType,
  extractTextBody,
  type BaileysSocket,
  type InboundEvent,
} from "./inbound.js";
import { normalizeToE164 } from "../utils/phone.js";

// ─── Helper: create a mock Baileys socket ───────────────────────────────────────

function createMockSocket(): BaileysSocket & { emit: (event: string, data: unknown) => void } {
  const handlers: Record<string, Array<(...args: unknown[]) => void>> = {};

  return {
    ev: {
      on(event: string, handler: (...args: unknown[]) => void) {
        if (!handlers[event]) handlers[event] = [];
        handlers[event].push(handler);
      },
    },
    emit(event: string, data: unknown) {
      for (const h of handlers[event] ?? []) {
        h(data);
      }
    },
  };
}

// ─── Helper: create a Baileys-style message ─────────────────────────────────────

function makeMessage(opts: {
  remoteJid: string;
  id: string;
  fromMe?: boolean;
  conversation?: string;
  messageTimestamp?: number;
  messageContent?: Record<string, unknown>;
}) {
  return {
    key: {
      remoteJid: opts.remoteJid,
      id: opts.id,
      fromMe: opts.fromMe ?? false,
    },
    message: opts.messageContent ?? (opts.conversation !== undefined ? { conversation: opts.conversation } : {}),
    messageTimestamp: opts.messageTimestamp ?? 1700000000,
  };
}

// ─── Deduplication Tests ────────────────────────────────────────────────────────

describe("messages.upsert deduplication", () => {
  it("forwards only one event when the same (remoteJid, id) is received twice", async () => {
    const socket = createMockSocket();
    const forwarded: InboundEvent[] = [];
    const forwardFn = vi.fn(async (event: InboundEvent) => {
      forwarded.push(event);
    });

    registerInboundHandler(socket, forwardFn);

    const msg = makeMessage({
      remoteJid: "6281234567890@s.whatsapp.net",
      id: "MSG001",
      conversation: "Hello",
    });

    // First upsert
    socket.emit("messages.upsert", { messages: [msg] });
    // Second upsert with same (remoteJid, id)
    socket.emit("messages.upsert", { messages: [msg] });

    expect(forwardFn).toHaveBeenCalledTimes(1);
    expect(forwarded[0].baileys_message_id).toBe("MSG001");
  });

  it("forwards both events when different message ids are used", async () => {
    const socket = createMockSocket();
    const forwardFn = vi.fn(async () => {});

    registerInboundHandler(socket, forwardFn);

    const msg1 = makeMessage({
      remoteJid: "6281234567890@s.whatsapp.net",
      id: "MSG001",
      conversation: "Hello",
    });
    const msg2 = makeMessage({
      remoteJid: "6281234567890@s.whatsapp.net",
      id: "MSG002",
      conversation: "World",
    });

    socket.emit("messages.upsert", { messages: [msg1] });
    socket.emit("messages.upsert", { messages: [msg2] });

    expect(forwardFn).toHaveBeenCalledTimes(2);
  });
});

// ─── fromMe Filter Tests ────────────────────────────────────────────────────────

describe("messages.upsert fromMe filter", () => {
  it("filters out messages with fromMe === true", async () => {
    const socket = createMockSocket();
    const forwardFn = vi.fn(async () => {});

    registerInboundHandler(socket, forwardFn);

    const msg = makeMessage({
      remoteJid: "6281234567890@s.whatsapp.net",
      id: "MSG003",
      fromMe: true,
      conversation: "My own message",
    });

    socket.emit("messages.upsert", { messages: [msg] });

    expect(forwardFn).not.toHaveBeenCalled();
  });

  it("forwards messages with fromMe === false", async () => {
    const socket = createMockSocket();
    const forwardFn = vi.fn(async () => {});

    registerInboundHandler(socket, forwardFn);

    const msg = makeMessage({
      remoteJid: "6281234567890@s.whatsapp.net",
      id: "MSG004",
      fromMe: false,
      conversation: "Customer message",
    });

    socket.emit("messages.upsert", { messages: [msg] });

    expect(forwardFn).toHaveBeenCalledTimes(1);
  });
});

// ─── Phone Normalization Tests ──────────────────────────────────────────────────

describe("normalizeToE164", () => {
  it("normalizes a Baileys individual JID to E.164", () => {
    expect(normalizeToE164("6281234567890@s.whatsapp.net")).toBe("+6281234567890");
  });

  it("returns null for group JIDs", () => {
    expect(normalizeToE164("120363123456789@g.us")).toBeNull();
  });

  it("returns null for numbers shorter than 8 digits", () => {
    expect(normalizeToE164("1234567@s.whatsapp.net")).toBeNull();
  });

  it("returns null for numbers longer than 15 digits", () => {
    expect(normalizeToE164("1234567890123456@s.whatsapp.net")).toBeNull();
  });

  it("is idempotent on already-normalized E.164 strings", () => {
    expect(normalizeToE164("+6281234567890")).toBe("+6281234567890");
  });

  it("handles bare number strings", () => {
    expect(normalizeToE164("6281234567890")).toBe("+6281234567890");
  });

  it("returns null for empty string", () => {
    expect(normalizeToE164("")).toBeNull();
  });
});

// ─── Message Type Classification Tests ──────────────────────────────────────────

describe("classifyMessageType", () => {
  it("classifies text conversation messages", () => {
    expect(classifyMessageType({ conversation: "hello" })).toBe("text");
  });

  it("classifies extended text messages", () => {
    expect(classifyMessageType({ extendedTextMessage: { text: "hello" } })).toBe("text");
  });

  it("classifies image messages", () => {
    expect(classifyMessageType({ imageMessage: { url: "..." } })).toBe("image");
  });

  it("classifies audio messages", () => {
    expect(classifyMessageType({ audioMessage: { url: "..." } })).toBe("audio");
  });

  it("classifies video messages", () => {
    expect(classifyMessageType({ videoMessage: { url: "..." } })).toBe("video");
  });

  it("classifies document messages", () => {
    expect(classifyMessageType({ documentMessage: { url: "..." } })).toBe("document");
  });

  it("classifies document with caption messages", () => {
    expect(classifyMessageType({ documentWithCaptionMessage: { message: {} } })).toBe("document");
  });

  it("classifies sticker messages", () => {
    expect(classifyMessageType({ stickerMessage: { url: "..." } })).toBe("sticker");
  });

  it("classifies location messages", () => {
    expect(classifyMessageType({ locationMessage: { lat: 0, lng: 0 } })).toBe("location");
  });

  it("classifies live location messages", () => {
    expect(classifyMessageType({ liveLocationMessage: { lat: 0, lng: 0 } })).toBe("location");
  });

  it("classifies unknown message types", () => {
    expect(classifyMessageType({ reactionMessage: {} })).toBe("unknown");
  });
});

// ─── Text Body Extraction Tests ─────────────────────────────────────────────────

describe("extractTextBody", () => {
  it("extracts text from conversation field", () => {
    expect(extractTextBody({ conversation: "Hello world" })).toBe("Hello world");
  });

  it("extracts text from extendedTextMessage", () => {
    expect(extractTextBody({ extendedTextMessage: { text: "Extended text" } })).toBe("Extended text");
  });

  it("returns null for non-text messages", () => {
    expect(extractTextBody({ imageMessage: { url: "..." } })).toBeNull();
  });
});

// ─── BoundedLRU Tests ───────────────────────────────────────────────────────────

describe("BoundedLRU", () => {
  it("reports false for unseen keys", () => {
    const lru = new BoundedLRU(5);
    expect(lru.has("key1")).toBe(false);
  });

  it("reports true for seen keys after add", () => {
    const lru = new BoundedLRU(5);
    lru.add("key1");
    expect(lru.has("key1")).toBe(true);
  });

  it("evicts oldest entry when at capacity", () => {
    const lru = new BoundedLRU(3);
    lru.add("a");
    lru.add("b");
    lru.add("c");
    // Adding a 4th should evict "a"
    lru.add("d");
    expect(lru.has("a")).toBe(false);
    expect(lru.has("b")).toBe(true);
    expect(lru.has("d")).toBe(true);
    expect(lru.size).toBe(3);
  });

  it("refreshes recently accessed keys (LRU behavior)", () => {
    const lru = new BoundedLRU(3);
    lru.add("a");
    lru.add("b");
    lru.add("c");
    // Access "a" to refresh it
    lru.has("a");
    // Adding "d" should evict "b" (oldest non-refreshed)
    lru.add("d");
    expect(lru.has("a")).toBe(true);
    expect(lru.has("b")).toBe(false);
  });
});

// ─── Integration: InboundEvent payload structure ────────────────────────────────

describe("InboundEvent payload", () => {
  it("produces a valid InboundEvent with correct fields", async () => {
    const socket = createMockSocket();
    const forwarded: InboundEvent[] = [];
    const forwardFn = vi.fn(async (event: InboundEvent) => {
      forwarded.push(event);
    });

    registerInboundHandler(socket, forwardFn);

    socket.emit("messages.upsert", {
      messages: [
        makeMessage({
          remoteJid: "6281234567890@s.whatsapp.net",
          id: "MSG100",
          conversation: "Hi there!",
          messageTimestamp: 1700000000,
        }),
      ],
    });

    expect(forwarded).toHaveLength(1);
    const event = forwarded[0];
    expect(event.baileys_message_id).toBe("MSG100");
    expect(event.sender_jid).toBe("6281234567890@s.whatsapp.net");
    expect(event.sender_phone_e164).toBe("+6281234567890");
    expect(event.message_type).toBe("text");
    expect(event.text_body).toBe("Hi there!");
    expect(event.event_timestamp).toBe(1700000000);
    expect(event.raw_key).toEqual({
      remoteJid: "6281234567890@s.whatsapp.net",
      id: "MSG100",
      fromMe: false,
    });
  });

  it("skips group messages (cannot normalize to E.164)", async () => {
    const socket = createMockSocket();
    const forwardFn = vi.fn(async () => {});

    registerInboundHandler(socket, forwardFn);

    socket.emit("messages.upsert", {
      messages: [
        makeMessage({
          remoteJid: "120363123456789@g.us",
          id: "GRPMSG001",
          conversation: "Group message",
        }),
      ],
    });

    expect(forwardFn).not.toHaveBeenCalled();
  });
});

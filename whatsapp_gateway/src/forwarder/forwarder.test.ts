import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { createForwarder, type Forwarder } from "./forwarder.js";
import type { InboundEvent } from "../baileys/inbound.js";
import { createServer, type Server, type IncomingMessage, type ServerResponse } from "node:http";
import { existsSync } from "node:fs";
import { readFile, rm, mkdir } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import pino from "pino";

// ─── Test Helpers ───────────────────────────────────────────────────────────────

function makeEvent(overrides?: Partial<InboundEvent>): InboundEvent {
  return {
    baileys_message_id: "msg-001",
    sender_jid: "6281234567890@s.whatsapp.net",
    sender_phone_e164: "+6281234567890",
    message_type: "text",
    text_body: "Hello",
    event_timestamp: Math.floor(Date.now() / 1000),
    raw_key: {
      remoteJid: "6281234567890@s.whatsapp.net",
      id: "msg-001",
      fromMe: false,
    },
    ...overrides,
  };
}

function createMockServer(handler: (req: IncomingMessage, res: ServerResponse) => void): Promise<{ server: Server; port: number }> {
  return new Promise((resolve) => {
    const server = createServer(handler);
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      const port = typeof addr === "object" && addr ? addr.port : 0;
      resolve({ server, port });
    });
  });
}

function closeServer(server: Server): Promise<void> {
  return new Promise((resolve) => {
    server.close(() => resolve());
  });
}

const silentLogger = pino({ level: "silent" });

// ─── Tests ──────────────────────────────────────────────────────────────────────

describe("Forwarder", () => {
  let outboxDir: string;
  let forwarder: Forwarder;
  let server: Server;
  let port: number;

  beforeEach(async () => {
    outboxDir = path.join(os.tmpdir(), `forwarder-test-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    await mkdir(outboxDir, { recursive: true });
  });

  afterEach(async () => {
    forwarder?.stopScanner();
    if (server) await closeServer(server);
    if (existsSync(outboxDir)) {
      await rm(outboxDir, { recursive: true, force: true });
    }
  });

  it("happy path: forwards event on first attempt with no overflow", async () => {
    let receivedBody: InboundEvent | null = null;
    let receivedAuth = "";

    ({ server, port } = await createMockServer((req, res) => {
      receivedAuth = req.headers.authorization ?? "";
      let body = "";
      req.on("data", (chunk) => { body += chunk; });
      req.on("end", () => {
        receivedBody = JSON.parse(body);
        res.writeHead(200);
        res.end();
      });
    }));

    forwarder = createForwarder({
      backendUrl: `http://127.0.0.1:${port}`,
      token: "test-token-123",
      outboxDir,
      timeoutMs: 5000,
      baseDelayMs: 10,
      maxDelayMs: 100,
      logger: silentLogger,
    });

    const event = makeEvent();
    await forwarder.forward(event);

    expect(receivedBody).toEqual(event);
    expect(receivedAuth).toBe("Bearer test-token-123");

    // No overflow file should exist
    const overflowPath = path.join(outboxDir, "overflow.ndjson");
    expect(existsSync(overflowPath)).toBe(false);
  });

  it("retries on failure and persists to overflow after max attempts exhausted", async () => {
    let attemptCount = 0;

    ({ server, port } = await createMockServer((_req, res) => {
      attemptCount++;
      res.writeHead(500);
      res.end("Internal Server Error");
    }));

    forwarder = createForwarder({
      backendUrl: `http://127.0.0.1:${port}`,
      token: "test-token-123",
      outboxDir,
      timeoutMs: 5000,
      maxAttempts: 3,
      baseDelayMs: 10, // Fast retries for testing
      maxDelayMs: 100,
      logger: silentLogger,
    });

    const event = makeEvent();
    await forwarder.forward(event);

    // Should have attempted 3 times
    expect(attemptCount).toBe(3);

    // Event should be in the overflow file
    const overflowPath = path.join(outboxDir, "overflow.ndjson");
    expect(existsSync(overflowPath)).toBe(true);

    const content = await readFile(overflowPath, "utf-8");
    const lines = content.trim().split("\n");
    expect(lines).toHaveLength(1);
    expect(JSON.parse(lines[0])).toEqual(event);
  });

  it("overflow scanner re-delivers events when backend recovers", async () => {
    let attemptCount = 0;
    let shouldFail = true;
    let deliveredEvents: InboundEvent[] = [];

    ({ server, port } = await createMockServer((req, res) => {
      attemptCount++;
      if (shouldFail) {
        res.writeHead(500);
        res.end("Internal Server Error");
        return;
      }
      let body = "";
      req.on("data", (chunk) => { body += chunk; });
      req.on("end", () => {
        deliveredEvents.push(JSON.parse(body));
        res.writeHead(200);
        res.end();
      });
    }));

    forwarder = createForwarder({
      backendUrl: `http://127.0.0.1:${port}`,
      token: "test-token-123",
      outboxDir,
      timeoutMs: 5000,
      maxAttempts: 3,
      baseDelayMs: 10,
      maxDelayMs: 100,
      scanIntervalMs: 60_000, // We'll flush manually
      logger: silentLogger,
    });

    // Forward event — will fail all 3 attempts and go to overflow
    const event = makeEvent();
    await forwarder.forward(event);

    const overflowPath = path.join(outboxDir, "overflow.ndjson");
    expect(existsSync(overflowPath)).toBe(true);

    // Simulate backend recovery
    shouldFail = false;
    attemptCount = 0;

    // Manually trigger overflow flush
    await forwarder.flushOverflow();

    // Event should have been re-delivered
    expect(deliveredEvents).toHaveLength(1);
    expect(deliveredEvents[0]).toEqual(event);

    // Overflow file should be removed (queue drained)
    expect(existsSync(overflowPath)).toBe(false);
  });

  it("overflow scanner keeps events that still fail re-delivery", async () => {
    ({ server, port } = await createMockServer((_req, res) => {
      res.writeHead(500);
      res.end("Internal Server Error");
    }));

    forwarder = createForwarder({
      backendUrl: `http://127.0.0.1:${port}`,
      token: "test-token-123",
      outboxDir,
      timeoutMs: 5000,
      maxAttempts: 3,
      baseDelayMs: 10,
      maxDelayMs: 100,
      scanIntervalMs: 60_000,
      logger: silentLogger,
    });

    // Forward two events — both will go to overflow
    const event1 = makeEvent({ baileys_message_id: "msg-001" });
    const event2 = makeEvent({ baileys_message_id: "msg-002", raw_key: { remoteJid: "6281234567890@s.whatsapp.net", id: "msg-002", fromMe: false } });

    await forwarder.forward(event1);
    await forwarder.forward(event2);

    const overflowPath = path.join(outboxDir, "overflow.ndjson");
    expect(existsSync(overflowPath)).toBe(true);

    // Flush while backend is still down
    await forwarder.flushOverflow();

    // Events should still be in the overflow file
    expect(existsSync(overflowPath)).toBe(true);
    const content = await readFile(overflowPath, "utf-8");
    const lines = content.trim().split("\n");
    expect(lines).toHaveLength(2);
  });

  it("succeeds on second attempt without overflow", async () => {
    let attemptCount = 0;

    ({ server, port } = await createMockServer((_req, res) => {
      attemptCount++;
      if (attemptCount === 1) {
        res.writeHead(500);
        res.end("Internal Server Error");
        return;
      }
      res.writeHead(200);
      res.end();
    }));

    forwarder = createForwarder({
      backendUrl: `http://127.0.0.1:${port}`,
      token: "test-token-123",
      outboxDir,
      timeoutMs: 5000,
      maxAttempts: 3,
      baseDelayMs: 10,
      maxDelayMs: 100,
      logger: silentLogger,
    });

    const event = makeEvent();
    await forwarder.forward(event);

    expect(attemptCount).toBe(2);

    // No overflow file should exist
    const overflowPath = path.join(outboxDir, "overflow.ndjson");
    expect(existsSync(overflowPath)).toBe(false);
  });
});

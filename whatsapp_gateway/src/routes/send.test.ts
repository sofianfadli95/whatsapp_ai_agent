import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import Fastify, { type FastifyInstance } from "fastify";
import { sendRoute } from "./send.js";
import type { SessionManagerInterface } from "../baileys/session.js";

const TEST_TOKEN = "test-secret-token-12345";

/**
 * Fake SessionManager for testing the /send route.
 */
class FakeSessionManager implements SessionManagerInterface {
  private _connected = false;
  private _socket: any = null;

  getSocket() {
    return this._socket;
  }

  isConnected(): boolean {
    return this._connected;
  }

  currentQR(): string | null {
    return null;
  }

  async start(): Promise<void> {
    // no-op for tests
  }

  stop(): void {
    // no-op for tests
  }

  // Test helpers
  setConnected(connected: boolean): void {
    this._connected = connected;
  }

  setSocket(socket: any): void {
    this._socket = socket;
  }
}

function makeAuthHeaders() {
  return { authorization: `Bearer ${TEST_TOKEN}` };
}

function makeValidBody(overrides: Record<string, unknown> = {}) {
  return {
    to: "+6281234567890",
    body: "Hello, world!",
    idempotency_key: `key-${Date.now()}-${Math.random()}`,
    ...overrides,
  };
}

describe("POST /send", () => {
  let app: FastifyInstance;
  let fakeSession: FakeSessionManager;

  beforeEach(async () => {
    process.env.WHATSAPP_GATEWAY_INTERNAL_TOKEN = TEST_TOKEN;

    fakeSession = new FakeSessionManager();

    app = Fastify();
    await app.register(sendRoute, { sessionManager: fakeSession });
    await app.ready();
  });

  afterEach(async () => {
    await app.close();
    delete process.env.WHATSAPP_GATEWAY_INTERNAL_TOKEN;
    vi.restoreAllMocks();
  });

  it("returns 401 when bearer token is missing", async () => {
    const response = await app.inject({
      method: "POST",
      url: "/send",
      payload: makeValidBody(),
    });

    expect(response.statusCode).toBe(401);
  });

  it("returns 400 for invalid E.164 phone number", async () => {
    const response = await app.inject({
      method: "POST",
      url: "/send",
      headers: makeAuthHeaders(),
      payload: makeValidBody({ to: "not-a-phone" }),
    });

    expect(response.statusCode).toBe(400);
    const body = response.json();
    expect(body.status).toBe("validation_error");
  });

  it("returns 400 for empty message body", async () => {
    const response = await app.inject({
      method: "POST",
      url: "/send",
      headers: makeAuthHeaders(),
      payload: makeValidBody({ body: "" }),
    });

    expect(response.statusCode).toBe(400);
    const body = response.json();
    expect(body.status).toBe("validation_error");
  });

  it("returns 503 when socket is not connected", async () => {
    fakeSession.setConnected(false);

    const response = await app.inject({
      method: "POST",
      url: "/send",
      headers: makeAuthHeaders(),
      payload: makeValidBody(),
    });

    expect(response.statusCode).toBe(503);
    const body = response.json();
    expect(body.status).toBe("not_connected");
  });

  it("returns 200 with message_id on successful send", async () => {
    const mockMessageId = "ABCDEF123456";
    const mockSocket = {
      sendMessage: vi.fn().mockResolvedValue({
        key: { id: mockMessageId },
      }),
    };

    fakeSession.setConnected(true);
    fakeSession.setSocket(mockSocket);

    const response = await app.inject({
      method: "POST",
      url: "/send",
      headers: makeAuthHeaders(),
      payload: makeValidBody(),
    });

    expect(response.statusCode).toBe(200);
    const body = response.json();
    expect(body.status).toBe("sent");
    expect(body.message_id).toBe(mockMessageId);
    expect(mockSocket.sendMessage).toHaveBeenCalledWith(
      "6281234567890@s.whatsapp.net",
      { text: "Hello, world!" },
    );
  });

  it("returns the same response for duplicate idempotency_key", async () => {
    const mockMessageId = "REPLAY_MSG_001";
    const mockSocket = {
      sendMessage: vi.fn().mockResolvedValue({
        key: { id: mockMessageId },
      }),
    };

    fakeSession.setConnected(true);
    fakeSession.setSocket(mockSocket);

    const idempotencyKey = "unique-key-for-replay-test";
    const payload = makeValidBody({ idempotency_key: idempotencyKey });

    // First request
    const response1 = await app.inject({
      method: "POST",
      url: "/send",
      headers: makeAuthHeaders(),
      payload,
    });

    expect(response1.statusCode).toBe(200);
    const body1 = response1.json();
    expect(body1.status).toBe("sent");
    expect(body1.message_id).toBe(mockMessageId);

    // Second request with same idempotency key
    const response2 = await app.inject({
      method: "POST",
      url: "/send",
      headers: makeAuthHeaders(),
      payload,
    });

    expect(response2.statusCode).toBe(200);
    const body2 = response2.json();
    expect(body2.status).toBe("sent");
    expect(body2.message_id).toBe(mockMessageId);

    // sendMessage should only have been called once
    expect(mockSocket.sendMessage).toHaveBeenCalledTimes(1);
  });

  it("returns 504 on send timeout", async () => {
    const mockSocket = {
      sendMessage: vi.fn().mockImplementation(
        () => new Promise((resolve) => setTimeout(resolve, 15_000)),
      ),
    };

    fakeSession.setConnected(true);
    fakeSession.setSocket(mockSocket);

    // Use fake timers to avoid waiting 10s in tests
    vi.useFakeTimers();

    const responsePromise = app.inject({
      method: "POST",
      url: "/send",
      headers: makeAuthHeaders(),
      payload: makeValidBody({ idempotency_key: "timeout-test-key" }),
    });

    // Advance time past the 10s timeout
    await vi.advanceTimersByTimeAsync(11_000);

    const response = await responsePromise;

    expect(response.statusCode).toBe(504);
    const body = response.json();
    expect(body.status).toBe("timeout");

    vi.useRealTimers();
  });

  it("returns 502 on Baileys send error", async () => {
    const mockSocket = {
      sendMessage: vi.fn().mockRejectedValue(new Error("connection_closed")),
    };

    fakeSession.setConnected(true);
    fakeSession.setSocket(mockSocket);

    const response = await app.inject({
      method: "POST",
      url: "/send",
      headers: makeAuthHeaders(),
      payload: makeValidBody({ idempotency_key: "error-test-key" }),
    });

    expect(response.statusCode).toBe(502);
    const body = response.json();
    expect(body.status).toBe("send_failed");
    expect(body.error_code).toBe("connection_closed");
  });
});

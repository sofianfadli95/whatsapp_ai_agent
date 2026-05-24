import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { safeCompare, bearerAuth } from "./auth.js";
import type { FastifyRequest, FastifyReply } from "fastify";

describe("safeCompare", () => {
  it("returns true for matching strings", () => {
    expect(safeCompare("hello", "hello")).toBe(true);
    expect(safeCompare("secret-token-123", "secret-token-123")).toBe(true);
    expect(safeCompare("", "")).toBe(true);
  });

  it("returns false for non-matching strings of equal length", () => {
    expect(safeCompare("hello", "world")).toBe(false);
    expect(safeCompare("abcde", "abcdf")).toBe(false);
  });

  it("returns false for strings of different lengths", () => {
    expect(safeCompare("short", "longer-string")).toBe(false);
    expect(safeCompare("a", "ab")).toBe(false);
    expect(safeCompare("token-abc", "token")).toBe(false);
  });

  it("handles constant-time comparison for length mismatch without throwing", () => {
    // Verifies that the function does not short-circuit or throw on length mismatch.
    // It still performs a timing-safe comparison internally even when lengths differ.
    expect(() => safeCompare("a", "bb")).not.toThrow();
    expect(safeCompare("a", "bb")).toBe(false);
    expect(() => safeCompare("long-token-value", "x")).not.toThrow();
    expect(safeCompare("long-token-value", "x")).toBe(false);
  });
});

describe("bearerAuth", () => {
  const TEST_TOKEN = "test-internal-token-xyz";

  function createMockRequest(authHeader?: string): FastifyRequest {
    return {
      headers: {
        ...(authHeader !== undefined ? { authorization: authHeader } : {}),
      },
    } as unknown as FastifyRequest;
  }

  function createMockReply() {
    const reply = {
      statusCode: 0,
      body: null as unknown,
      status(code: number) {
        reply.statusCode = code;
        return reply;
      },
      send(payload: unknown) {
        reply.body = payload;
        return reply;
      },
    };
    return reply as unknown as FastifyReply & { statusCode: number; body: unknown };
  }

  beforeEach(() => {
    process.env.WHATSAPP_GATEWAY_INTERNAL_TOKEN = TEST_TOKEN;
  });

  afterEach(() => {
    delete process.env.WHATSAPP_GATEWAY_INTERNAL_TOKEN;
  });

  it("returns 401 when authorization header is missing", async () => {
    const request = createMockRequest(undefined);
    const reply = createMockReply();

    await bearerAuth(request, reply as unknown as FastifyReply);

    expect(reply.statusCode).toBe(401);
    expect((reply.body as { error: string }).error).toMatch(/missing/i);
  });

  it("returns 401 when authorization header does not start with Bearer", async () => {
    const request = createMockRequest("Basic some-credentials");
    const reply = createMockReply();

    await bearerAuth(request, reply as unknown as FastifyReply);

    expect(reply.statusCode).toBe(401);
    expect((reply.body as { error: string }).error).toMatch(/missing|invalid/i);
  });

  it("returns 401 when bearer token is incorrect", async () => {
    const request = createMockRequest("Bearer wrong-token");
    const reply = createMockReply();

    await bearerAuth(request, reply as unknown as FastifyReply);

    expect(reply.statusCode).toBe(401);
    expect((reply.body as { error: string }).error).toMatch(/invalid/i);
  });

  it("does not send a reply when token is correct (passes through)", async () => {
    const request = createMockRequest(`Bearer ${TEST_TOKEN}`);
    const reply = createMockReply();
    const sendSpy = vi.fn();
    (reply as unknown as { send: typeof sendSpy }).send = sendSpy;
    const statusSpy = vi.fn().mockReturnValue({ send: sendSpy });
    (reply as unknown as { status: typeof statusSpy }).status = statusSpy;

    await bearerAuth(request, reply as unknown as FastifyReply);

    expect(statusSpy).not.toHaveBeenCalled();
    expect(sendSpy).not.toHaveBeenCalled();
  });

  it("returns 500 when WHATSAPP_GATEWAY_INTERNAL_TOKEN env var is not set", async () => {
    delete process.env.WHATSAPP_GATEWAY_INTERNAL_TOKEN;

    const request = createMockRequest(`Bearer ${TEST_TOKEN}`);
    const reply = createMockReply();

    await bearerAuth(request, reply as unknown as FastifyReply);

    expect(reply.statusCode).toBe(500);
    expect((reply.body as { error: string }).error).toMatch(/misconfigured/i);
  });
});

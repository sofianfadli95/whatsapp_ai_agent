import { timingSafeEqual } from "node:crypto";
import type { FastifyRequest, FastifyReply } from "fastify";

/**
 * Constant-time string comparison to prevent timing attacks on bearer tokens.
 */
export function safeCompare(a: string, b: string): boolean {
  if (a.length !== b.length) {
    // Still do a comparison to avoid short-circuit timing leak on length
    const buf = Buffer.alloc(Math.max(a.length, b.length));
    timingSafeEqual(buf, buf);
    return false;
  }
  return timingSafeEqual(Buffer.from(a, "utf-8"), Buffer.from(b, "utf-8"));
}

/**
 * Fastify preHandler hook that verifies the Authorization: Bearer <token> header
 * against the WHATSAPP_GATEWAY_INTERNAL_TOKEN environment variable.
 */
export async function bearerAuth(
  request: FastifyRequest,
  reply: FastifyReply,
): Promise<void> {
  const expectedToken = process.env.WHATSAPP_GATEWAY_INTERNAL_TOKEN;

  if (!expectedToken) {
    reply.status(500).send({ error: "Server misconfigured: missing internal token" });
    return;
  }

  const authHeader = request.headers.authorization;

  if (!authHeader || !authHeader.startsWith("Bearer ")) {
    reply.status(401).send({ error: "Missing or invalid authorization header" });
    return;
  }

  const token = authHeader.slice(7); // Remove "Bearer " prefix

  if (!safeCompare(token, expectedToken)) {
    reply.status(401).send({ error: "Invalid bearer token" });
    return;
  }
}

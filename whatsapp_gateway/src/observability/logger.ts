/**
 * Structured pino logger with request-id correlation.
 * Log fields include: event_id, gateway_action, to_phone, status.
 */

import pino from "pino";

export interface GatewayLogFields {
  event_id?: string;
  gateway_action?: string;
  to_phone?: string;
  status?: string;
  request_id?: string;
}

/**
 * Creates a configured pino logger instance for the WhatsApp Gateway.
 * Uses JSON output with request-id correlation support.
 */
export function createLogger(name = "whatsapp-gateway"): pino.Logger {
  return pino({
    name,
    level: process.env.LOG_LEVEL ?? "info",
    formatters: {
      level(label) {
        return { level: label };
      },
    },
    timestamp: pino.stdTimeFunctions.isoTime,
  });
}

/**
 * Fastify logger options for request-id correlation.
 * Pass this to Fastify's `logger` option to get structured JSON logs
 * with automatic request-id in every log line.
 */
export const fastifyLoggerOptions = {
  level: process.env.LOG_LEVEL ?? "info",
  formatters: {
    level(label: string) {
      return { level: label };
    },
  },
  timestamp: pino.stdTimeFunctions.isoTime,
  serializers: {
    req(request: { method: string; url: string; id: string }) {
      return {
        method: request.method,
        url: request.url,
        request_id: request.id,
      };
    },
    res(reply: { statusCode: number }) {
      return {
        statusCode: reply.statusCode,
      };
    },
  },
};

/** Singleton logger instance for use outside of request context */
export const logger = createLogger();

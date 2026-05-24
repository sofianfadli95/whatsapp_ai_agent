import type { FastifyInstance } from "fastify";
import QRCode from "qrcode";
import { bearerAuth } from "../middleware/auth.js";
import type { SessionManagerInterface } from "../baileys/session.js";

export interface QrRouteOptions {
  sessionManager: SessionManagerInterface;
}

/**
 * Registers the GET /qr route.
 *
 * - Returns { "qr_data_url": "data:image/png;base64,..." } when a QR code is available (pairing pending).
 * - Returns { "status": "connected" } when the session is already paired and connected.
 * - Gated by bearer token authentication.
 */
export async function qrRoute(
  app: FastifyInstance,
  opts: QrRouteOptions,
): Promise<void> {
  const { sessionManager } = opts;

  app.get(
    "/qr",
    { preHandler: [bearerAuth] },
    async (_request, reply) => {
      if (sessionManager.isConnected()) {
        return reply.status(200).send({ status: "connected" });
      }

      const qrText = sessionManager.currentQR();

      if (!qrText) {
        return reply.status(503).send({
          error: "No QR code available. Session may be initializing.",
        });
      }

      // Convert the QR text to a data URL (PNG base64)
      const dataUrl = await QRCode.toDataURL(qrText, {
        type: "image/png",
        margin: 2,
        width: 256,
      });

      return reply.status(200).send({ qr_data_url: dataUrl });
    },
  );
}

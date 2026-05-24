import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  type WASocket,
  type ConnectionState,
} from "@whiskeysockets/baileys";
import pino from "pino";

const logger = pino({ level: process.env.LOG_LEVEL ?? "info" });

export interface SessionManagerInterface {
  getSocket(): WASocket | null;
  isConnected(): boolean;
  currentQR(): string | null;
  start(): Promise<void>;
  stop(): void;
}

/**
 * SessionManager singleton that manages the Baileys WhatsApp Web session.
 * Handles QR code generation, connection state, and automatic reconnection.
 */
export class SessionManager implements SessionManagerInterface {
  private socket: WASocket | null = null;
  private connected = false;
  private qrCode: string | null = null;
  private authDir: string;
  private stopped = false;

  constructor(authDir?: string) {
    this.authDir = authDir ?? process.env.WHATSAPP_GATEWAY_AUTH_DIR ?? "./auth_state";
  }

  getSocket(): WASocket | null {
    return this.socket;
  }

  isConnected(): boolean {
    return this.connected;
  }

  currentQR(): string | null {
    return this.qrCode;
  }

  async start(): Promise<void> {
    this.stopped = false;
    await this.createSocket();
  }

  stop(): void {
    this.stopped = true;
    if (this.socket) {
      this.socket.end(undefined);
      this.socket = null;
    }
    this.connected = false;
    this.qrCode = null;
  }

  private async createSocket(): Promise<void> {
    const { state, saveCreds } = await useMultiFileAuthState(this.authDir);

    const sock = makeWASocket({
      auth: state,
      logger: logger.child({ module: "baileys" }) as any,
      printQRInTerminal: true,
    });

    sock.ev.on("creds.update", saveCreds);

    sock.ev.on("connection.update", (update: Partial<ConnectionState>) => {
      this.handleConnectionUpdate(update);
    });

    this.socket = sock;
  }

  private handleConnectionUpdate(update: Partial<ConnectionState>): void {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      this.qrCode = qr;
      this.connected = false;
    }

    if (connection === "close") {
      this.connected = false;
      this.qrCode = null;

      const statusCode =
        (lastDisconnect?.error as any)?.output?.statusCode as number | undefined;

      if (statusCode === DisconnectReason.loggedOut) {
        // Logged out — do not reconnect, wait for new QR scan
        this.socket = null;
        if (!this.stopped) {
          // Restart to generate a new QR code
          void this.createSocket();
        }
      } else if (!this.stopped) {
        // Reconnect on non-loggedOut disconnections
        void this.createSocket();
      }
    }

    if (connection === "open") {
      this.connected = true;
      this.qrCode = null;
    }
  }
}

// Singleton instance
let instance: SessionManager | null = null;

export function getSessionManager(authDir?: string): SessionManager {
  if (!instance) {
    instance = new SessionManager(authDir);
  }
  return instance;
}

// For testing: reset the singleton
export function resetSessionManager(): void {
  if (instance) {
    instance.stop();
  }
  instance = null;
}

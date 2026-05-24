import { describe, it, expect } from "vitest";
import { normalizeToE164 } from "./phone.js";

describe("normalizeToE164", () => {
  // ─── Idempotence ────────────────────────────────────────────────────────────

  describe("idempotence", () => {
    it("returns the same value when called on an already-normalized E.164 string", () => {
      const e164 = "+6281234567890";
      expect(normalizeToE164(e164)).toBe(e164);
    });

    it("is idempotent: normalizing twice yields the same result", () => {
      const jid = "6281234567890@s.whatsapp.net";
      const first = normalizeToE164(jid);
      const second = normalizeToE164(first!);
      expect(first).toBe(second);
    });

    it("is idempotent for a bare number", () => {
      const bare = "447911123456";
      const first = normalizeToE164(bare);
      const second = normalizeToE164(first!);
      expect(first).toBe(second);
    });
  });

  // ─── Valid JID Normalization ────────────────────────────────────────────────

  describe("valid JID normalization", () => {
    it("normalizes an individual JID to E.164", () => {
      expect(normalizeToE164("6281234567890@s.whatsapp.net")).toBe("+6281234567890");
    });

    it("normalizes a different country JID", () => {
      expect(normalizeToE164("447911123456@s.whatsapp.net")).toBe("+447911123456");
    });

    it("normalizes a US number JID", () => {
      expect(normalizeToE164("14155552671@s.whatsapp.net")).toBe("+14155552671");
    });
  });

  // ─── Bare Number Normalization ──────────────────────────────────────────────

  describe("bare number normalization", () => {
    it("normalizes a bare number string by prepending +", () => {
      expect(normalizeToE164("6281234567890")).toBe("+6281234567890");
    });

    it("normalizes a bare number with exactly 8 digits", () => {
      expect(normalizeToE164("12345678")).toBe("+12345678");
    });

    it("normalizes a bare number with exactly 15 digits", () => {
      expect(normalizeToE164("123456789012345")).toBe("+123456789012345");
    });
  });

  // ─── Invalid Rejection ──────────────────────────────────────────────────────

  describe("invalid rejection", () => {
    it("returns null for group JIDs (@g.us)", () => {
      expect(normalizeToE164("120363123456789@g.us")).toBeNull();
    });

    it("returns null for numbers shorter than 8 digits", () => {
      expect(normalizeToE164("1234567@s.whatsapp.net")).toBeNull();
    });

    it("returns null for bare numbers shorter than 8 digits", () => {
      expect(normalizeToE164("1234567")).toBeNull();
    });

    it("returns null for numbers longer than 15 digits", () => {
      expect(normalizeToE164("1234567890123456@s.whatsapp.net")).toBeNull();
    });

    it("returns null for bare numbers longer than 15 digits", () => {
      expect(normalizeToE164("1234567890123456")).toBeNull();
    });

    it("returns null for empty string", () => {
      expect(normalizeToE164("")).toBeNull();
    });

    it("returns null for a string with only non-digit characters", () => {
      expect(normalizeToE164("abcdefgh")).toBeNull();
    });
  });

  // ─── Edge Cases ─────────────────────────────────────────────────────────────

  describe("edge cases", () => {
    it("strips non-digit characters and normalizes", () => {
      // A number with dashes/spaces embedded
      expect(normalizeToE164("628-1234-567890@s.whatsapp.net")).toBe("+6281234567890");
    });

    it("strips non-digit characters from bare numbers", () => {
      expect(normalizeToE164("62 812 3456 7890")).toBe("+6281234567890");
    });

    it("handles boundary: exactly 8 digits in a JID", () => {
      expect(normalizeToE164("12345678@s.whatsapp.net")).toBe("+12345678");
    });

    it("handles boundary: exactly 15 digits in a JID", () => {
      expect(normalizeToE164("123456789012345@s.whatsapp.net")).toBe("+123456789012345");
    });

    it("rejects 7 digits (just below minimum)", () => {
      expect(normalizeToE164("1234567")).toBeNull();
    });

    it("rejects 16 digits (just above maximum)", () => {
      expect(normalizeToE164("1234567890123456")).toBeNull();
    });

    it("handles E.164 string with + prefix that has exactly 8 digits", () => {
      expect(normalizeToE164("+12345678")).toBe("+12345678");
    });

    it("handles E.164 string with + prefix that has exactly 15 digits", () => {
      expect(normalizeToE164("+123456789012345")).toBe("+123456789012345");
    });
  });
});

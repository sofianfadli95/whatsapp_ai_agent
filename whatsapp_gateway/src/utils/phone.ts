/**
 * E.164 phone number normalization utility.
 * Extracts phone numbers from Baileys JIDs and normalizes to E.164 format.
 */

const INDIVIDUAL_SUFFIX = "@s.whatsapp.net";
const GROUP_SUFFIX = "@g.us";

/**
 * Normalizes a Baileys JID to E.164 format.
 *
 * - For individual chats (`<number>@s.whatsapp.net`), strips the suffix and prepends `+`.
 * - Validates the result is 8–15 digits (per E.164 spec).
 * - Returns `null` for group JIDs (`@g.us`) or invalid numbers.
 * - Idempotent: calling on an already-normalized E.164 string returns the same value.
 */
export function normalizeToE164(jid: string): string | null {
  if (!jid || typeof jid !== "string") {
    return null;
  }

  // Reject group JIDs
  if (jid.endsWith(GROUP_SUFFIX)) {
    return null;
  }

  let numberPart: string;

  if (jid.endsWith(INDIVIDUAL_SUFFIX)) {
    // Extract number from JID format: <number>@s.whatsapp.net
    numberPart = jid.slice(0, -INDIVIDUAL_SUFFIX.length);
  } else if (jid.startsWith("+")) {
    // Already in E.164 format (idempotent case)
    numberPart = jid.slice(1);
  } else {
    // Bare number without suffix or prefix
    numberPart = jid;
  }

  // Strip any non-digit characters
  const digits = numberPart.replace(/\D/g, "");

  // Validate E.164: 8–15 digits
  if (digits.length < 8 || digits.length > 15) {
    return null;
  }

  return `+${digits}`;
}

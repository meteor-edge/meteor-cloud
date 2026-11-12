/** Random password for SSH / login fields (browser crypto only; never sent to generate). */

const ALPHABET =
  "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*";

/** Default length matches a comfortable SSH password; Wi-Fi PSK must stay ≤ 63. */
export function generatePassword(length = 16): string {
  const size = Math.max(8, Math.min(length, 63));
  const bytes = new Uint8Array(size);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => ALPHABET[byte % ALPHABET.length]).join("");
}

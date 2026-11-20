/**
 * Webhook signature verification for Omnea webhooks in TypeScript/Node.js.
 */

import * as crypto from "crypto";

export interface VerifyWebhookOptions {
  payload: string | Buffer;
  signatureHeader: string;
  secret: string;
  toleranceSeconds?: number;
}

export function verifyWebhookSignature(options: VerifyWebhookOptions): boolean {
  const { payload, signatureHeader, secret, toleranceSeconds = 300 } = options;
  if (!signatureHeader || !secret) {
    return false;
  }

  const parts = Object.fromEntries(
    signatureHeader
      .split(",")
      .map((part) => part.split("=").map((s) => s.trim()))
      .filter((kv) => kv.length === 2)
  );

  const timestampStr = parts["t"];
  const signatureHex = parts["v1"];

  if (!timestampStr || !signatureHex) {
    return false;
  }

  const timestamp = parseInt(timestampStr, 10);
  if (isNaN(timestamp)) {
    return false;
  }

  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - timestamp) > toleranceSeconds) {
    return false; // Replay attack prevented
  }

  const rawBytes = typeof payload === "string" ? Buffer.from(payload, "utf-8") : payload;
  const hmac = crypto.createHmac("sha256", secret);
  hmac.update(`${timestamp}.`);
  hmac.update(rawBytes);
  const expectedSig = hmac.digest("hex");

  try {
    return crypto.timingSafeEqual(
      Buffer.from(expectedSig, "hex"),
      Buffer.from(signatureHex, "hex")
    );
  } catch {
    return false;
  }
}

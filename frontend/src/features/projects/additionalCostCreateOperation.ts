import type { AdditionalCostCreateRequest } from "../../api/contracts/additionalCosts";

function storageKey(projectId: string, scenarioId: string, bodyFingerprint: string): string {
  return `stafffingcalculator.pending-additional-cost:${projectId}:${scenarioId}:${bodyFingerprint}`;
}

async function fingerprint(body: AdditionalCostCreateRequest): Promise<string> {
  const serialized = JSON.stringify(body);
  if (typeof window.crypto.subtle?.digest === "function") {
    const bytes = new TextEncoder().encode(serialized);
    const digest = await window.crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  // Some test/browser contexts expose UUIDs but not SubtleCrypto. This local fingerprint only
  // matches retries; a collision is still refused by the backend's payload-fingerprint check.
  let first = 0x811c9dc5;
  let second = 0x811c9dc5;
  for (let index = 0; index < serialized.length; index += 1) {
    const character = serialized.charCodeAt(index);
    first = Math.imul(first ^ character, 0x01000193);
    second = Math.imul(second ^ (character + index), 0x01000193);
  }
  return `${(first >>> 0).toString(16)}${(second >>> 0).toString(16)}`;
}

interface PendingOperation {
  readonly key: string;
  readonly fingerprint: string;
}

function readPending(storage: Storage, slot: string): PendingOperation | null {
  try {
    const value: unknown = JSON.parse(storage.getItem(slot) ?? "null");
    if (
      typeof value === "object" && value !== null &&
      "key" in value && typeof value.key === "string" &&
      "fingerprint" in value && typeof value.fingerprint === "string"
    ) {
      return { key: value.key, fingerprint: value.fingerprint };
    }
  } catch {
    // Inaccessible or malformed session storage cannot provide a key; a new logical write gets one.
  }
  return null;
}

export async function keyForAdditionalCostCreate(
  projectId: string,
  scenarioId: string,
  body: AdditionalCostCreateRequest,
  storage: Storage = window.sessionStorage,
): Promise<string> {
  const bodyFingerprint = await fingerprint(body);
  const slot = storageKey(projectId, scenarioId, bodyFingerprint);
  const previous = readPending(storage, slot);
  if (previous?.fingerprint === bodyFingerprint) return previous.key;

  const key = window.crypto.randomUUID();
  try {
    storage.setItem(slot, JSON.stringify({ key, fingerprint: bodyFingerprint }));
  } catch {
    // The in-memory request can still be attempted. A retry after storage failure cannot be
    // guaranteed to retain the key, so the screen keeps its current form mounted for retry.
  }
  return key;
}

export function clearPendingAdditionalCostCreate(
  projectId: string,
  scenarioId: string,
  body: AdditionalCostCreateRequest,
  storage: Storage = window.sessionStorage,
): Promise<void> {
  return fingerprint(body).then((bodyFingerprint) => {
    try {
      storage.removeItem(storageKey(projectId, scenarioId, bodyFingerprint));
    } catch {
      // A later attempt will see the old key and the same body; backend replay remains safe.
    }
  });
}

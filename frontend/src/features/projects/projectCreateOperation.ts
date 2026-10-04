const STORAGE_KEY = "stafffingcalculator.pending-project-create-key";

export function readPendingProjectCreateKey(storage: Storage = window.sessionStorage): string | null {
  try {
    const key = storage.getItem(STORAGE_KEY);
    return key !== null && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(key)
      ? key
      : null;
  } catch {
    return null;
  }
}

export function newProjectCreateKey(): string {
  return window.crypto.randomUUID();
}

export function persistPendingProjectCreateKey(key: string, storage: Storage = window.sessionStorage): void {
  storage.setItem(STORAGE_KEY, key);
}

export function clearPendingProjectCreateKey(storage: Storage = window.sessionStorage): void {
  storage.removeItem(STORAGE_KEY);
}

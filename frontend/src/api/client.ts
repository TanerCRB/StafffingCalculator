import type { HealthResponse } from "./contracts/health";

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/health`);
  if (!response.ok) {
    throw new Error(`GET /health failed: ${response.status}`);
  }
  return (await response.json()) as HealthResponse;
}

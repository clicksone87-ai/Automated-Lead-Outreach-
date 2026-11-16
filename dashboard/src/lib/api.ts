import createClient from "openapi-fetch";
import type { components, paths } from "./api-types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const api = createClient<paths>({ baseUrl: API_URL });

const token = process.env.NEXT_PUBLIC_API_TOKEN;
if (token) {
  api.use({
    onRequest({ request }) {
      request.headers.set("Authorization", `Bearer ${token}`);
      return request;
    },
  });
}

export type Schemas = components["schemas"];
export type Overview = Schemas["OverviewOut"];
export type LeadRow = Schemas["LeadRow"];
export type LeadPage = Schemas["LeadPage"];
export type LeadDetail = Schemas["LeadDetail"];
export type SourceOut = Schemas["SourceOut"];
export type SettingsOut = Schemas["SettingsOut"];
export type Integration = Schemas["IntegrationOut"];
export type Activity = Schemas["ActivityOut"];
export type AgentStatus = Schemas["AgentStatus"];
export type Health = Schemas["HealthOut"];
export type Costs = Schemas["CostsOut"];
export type SearchHit = Schemas["SearchHit"];
export type AuditRow = Schemas["AuditRow"];
export type AuditPage = Schemas["AuditPage"];
export type AuditDetail = Schemas["AuditDetail"];
export type FreeTier = Schemas["FreeTierOut"];

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

/** Unwrap an openapi-fetch result, throwing a readable error. */
export async function unwrap<T>(
  promise: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await promise;
  if (error !== undefined || data === undefined) {
    const detail =
      error && typeof error === "object" && "detail" in error
        ? String((error as { detail: unknown }).detail)
        : response.statusText || "Request failed";
    throw new ApiError(detail, response.status);
  }
  return data;
}

export function fileUrl(path: string | null | undefined): string | null {
  return path ? `${API_URL}/api/files/${path.split("/").map(encodeURIComponent).join("/")}` : null;
}

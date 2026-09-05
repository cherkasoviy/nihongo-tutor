/**
 * Minimal typed fetch wrapper. Response shapes mirror backend/app/api/schemas.py; once the OpenAPI
 * spec is exported (`make openapi`), `npm run gen:api` regenerates src/api/schema.d.ts and these
 * hand-written types can be replaced by `components['schemas'][...]`.
 */

export interface UserOut {
  id: string;
  tg_user_id: number;
  first_name: string | null;
  tg_username: string | null;
  role: 'learner' | 'admin';
  status: 'active' | 'paused' | 'blocked';
  timezone: string;
  reminder_time: string | null;
  daily_minutes_target: number;
  furigana_mode: 'always' | 'auto' | 'off';
  onboarded_at: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
  user: UserOut;
}

export interface InviteOut {
  id: string;
  code: string;
  max_uses: number;
  uses: number;
  expires_at: string | null;
  revoked: boolean;
  note: string | null;
  created_at: string;
  start_link: string | null;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`${status}: ${detail}`);
  }
}

const BASE = import.meta.env.VITE_API_BASE ?? '';

async function request<T>(method: string, path: string, body?: unknown, token?: string): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === 'string') detail = data.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const postJson = <T>(path: string, body: unknown, token?: string) => request<T>('POST', path, body, token);
export const getJson = <T>(path: string, token: string) => request<T>('GET', path, undefined, token);
export const deleteJson = <T>(path: string, token: string) => request<T>('DELETE', path, undefined, token);

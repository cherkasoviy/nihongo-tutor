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

// --- Phase 1: kana, session, stats -----------------------------------------

export type KanaScript = 'hiragana' | 'katakana';
export type KanaKind = 'basic' | 'dakuten' | 'handakuten' | 'yoon';
export type CardState = 'new' | 'learning' | 'review' | 'relearning';

export interface KanaCell {
  item_id: string;
  char: string;
  script: KanaScript;
  cyrillic: string;
  row: string;
  kind: KanaKind;
  group_order: number;
  mnemonic_ru: string | null;
  example_word: string | null;
  example_reading: string | null;
  example_gloss_ru: string | null;
  introduced: boolean;
  state: CardState | null;
  retrievability: number | null;
  due: string | null;
  reps: number;
}

export type StepKind =
  | 'review_recog'
  | 'review_prod'
  | 'intro_item'
  | 'cloze'
  | 'listen_choose'
  | 'shadow'
  | 'speak'
  | 'roleplay'
  | 'wrapup';

export interface SessionStep {
  id: string;
  idx: number;
  kind: StepKind;
  status: 'pending' | 'shown' | 'answered' | 'skipped';
  mode: 'choice' | 'ack';
  prompt: string | null;
  char: string | null;
  cyrillic: string | null;
  mnemonic_ru: string | null;
  example_word: string | null;
  example_gloss_ru: string | null;
  choices: string[];
}

export interface SessionState {
  id: string;
  local_date: string;
  planned_steps: number;
  completed_steps: number;
  outcome: 'in_progress' | 'completed' | 'abandoned';
  current: SessionStep | null;
}

export interface AnswerResult {
  accepted: boolean;
  correct: boolean;
  correct_label: string;
  rating: number | null;
  session_finished: boolean;
  next: SessionStep | null;
}

export interface Stats {
  kana_total: number;
  kana_introduced: number;
  kana_known: number;
  due_now: number;
  reviews_7d: number;
  retention_7d: number | null;
  streak_current: number;
  streak_longest: number;
  freezes_available: number;
  sessions_completed: number;
  minutes_7d: number;
}

export const patchJson = <T>(path: string, body: unknown, token: string) =>
  request<T>('PATCH', path, body, token);

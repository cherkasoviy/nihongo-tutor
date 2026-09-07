/**
 * Exchanges Telegram initData for a short-lived backend JWT and keeps it fresh.
 */
import { getRawInitData } from '@/tg/init';
import { useAuthStore } from '@/store/auth';
import { postJson, type TokenResponse } from '@/api/client';

const REFRESH_SKEW_MS = 60_000;

/** The browser is the only part of the system that knows where the learner actually is. */
function detectTimezone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

export async function login(): Promise<TokenResponse> {
  const initData = getRawInitData();
  if (!initData) throw new Error('initData недоступна: откройте приложение из Telegram');
  const token = await postJson<TokenResponse>('/api/auth/telegram', {
    init_data: initData,
    timezone: detectTimezone(),
  });
  useAuthStore.getState().setSession(token);
  return token;
}

/** Returns a valid access token, re-logging in when it is about to expire. */
export async function ensureToken(): Promise<string> {
  const { accessToken, expiresAt } = useAuthStore.getState();
  if (accessToken && expiresAt && expiresAt - Date.now() > REFRESH_SKEW_MS) return accessToken;
  const fresh = await login();
  return fresh.access_token;
}

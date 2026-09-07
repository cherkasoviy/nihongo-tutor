import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  deleteJson,
  getJson,
  patchJson,
  postJson,
  type AnswerResult,
  type InviteOut,
  type KanaCell,
  type KanaScript,
  type RevealResult,
  type SessionState,
  type Stats,
  type UserOut,
} from '@/api/client';
import { ensureToken } from '@/tg/auth';

export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: async () => getJson<UserOut>('/api/auth/me', await ensureToken()),
  });
}

export function useInvites(enabled: boolean) {
  return useQuery({
    queryKey: ['admin', 'invites'],
    enabled,
    queryFn: async () => getJson<InviteOut[]>('/api/admin/invites', await ensureToken()),
  });
}

export function useCreateInvite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { max_uses: number; ttl_days: number | null; note?: string }) =>
      postJson<InviteOut>('/api/admin/invites', body, await ensureToken()),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['admin', 'invites'] }),
  });
}

export function useRevokeInvite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => deleteJson<InviteOut>(`/api/admin/invites/${id}`, await ensureToken()),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['admin', 'invites'] }),
  });
}

export interface SettingsPatch {
  timezone?: string;
  reminder_time?: string | null;
  clear_reminder?: boolean;
  daily_minutes_target?: number;
  furigana_mode?: string;
}

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: SettingsPatch) => patchJson<UserOut>('/api/settings', body, await ensureToken()),
    onSuccess: (user) => {
      qc.setQueryData(['me'], user);
      void qc.invalidateQueries({ queryKey: ['stats'] });
    },
  });
}

export function useKanaGrid(script?: KanaScript) {
  return useQuery({
    queryKey: ['kana', script ?? 'all'],
    queryFn: async () =>
      getJson<KanaCell[]>(`/api/content/kana${script ? `?script=${script}` : ''}`, await ensureToken()),
    staleTime: 60_000,
  });
}

export function useStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: async () => getJson<Stats>('/api/stats', await ensureToken()),
  });
}

/** Starts (or resumes) today's session. Idempotent server-side, so re-mounting is harmless. */
export function useStartSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => postJson<SessionState>('/api/session/today', {}, await ensureToken()),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['stats'] }),
  });
}

/** Uncovers a free-recall answer without answering it; the wait is what decides Hard. */
export function useRevealStep() {
  return useMutation({
    mutationFn: async (stepId: string) =>
      postJson<RevealResult>(`/api/session/steps/${stepId}/reveal`, {}, await ensureToken()),
  });
}

export function useAnswerStep() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { stepId: string; body: { choice?: number; self_grade?: string; acknowledged?: boolean } }) =>
      postJson<AnswerResult>(`/api/session/steps/${vars.stepId}/answer`, vars.body, await ensureToken()),
    onSuccess: (result) => {
      if (result.session_finished) {
        void qc.invalidateQueries({ queryKey: ['stats'] });
        void qc.invalidateQueries({ queryKey: ['kana'] });
      }
    },
  });
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { deleteJson, getJson, postJson, type InviteOut, type UserOut } from '@/api/client';
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

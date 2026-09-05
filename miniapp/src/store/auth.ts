import { create } from 'zustand';

import type { TokenResponse, UserOut } from '@/api/client';

interface AuthState {
  accessToken: string | null;
  expiresAt: number | null;
  user: UserOut | null;
  setSession: (token: TokenResponse) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: null,
  expiresAt: null,
  user: null,
  setSession: (token) =>
    set({ accessToken: token.access_token, expiresAt: Date.now() + token.expires_in * 1000, user: token.user }),
  clear: () => set({ accessToken: null, expiresAt: null, user: null }),
}));

import { useEffect, useState } from 'react';

import { ApiError } from '@/api/client';
import { useMe } from '@/api/hooks';
import { AdminInvites } from '@/features/admin/AdminInvites';
import { Home } from '@/features/session/Home';
import { Screen } from '@/features/shell/Screen';
import { getStartParam } from '@/tg/init';
import { login } from '@/tg/auth';
import { useAuthStore } from '@/store/auth';

type Tab = 'home' | 'admin';

export function App() {
  const [authError, setAuthError] = useState<string | null>(null);
  const user = useAuthStore((s) => s.user);
  const [tab, setTab] = useState<Tab>(getStartParam() === 'admin' ? 'admin' : 'home');

  useEffect(() => {
    login().catch((err: unknown) => {
      if (err instanceof ApiError && err.status === 403) {
        setAuthError('Ты ещё не зарегистрирована. Открой бота и отправь /start с кодом приглашения.');
      } else if (err instanceof ApiError && err.status === 401) {
        setAuthError('Не удалось подтвердить данные Telegram. Закрой и открой приложение заново.');
      } else {
        setAuthError('Сервер недоступен. Попробуй чуть позже.');
      }
    });
  }, []);

  const me = useMe();

  if (authError) {
    return (
      <Screen title="Nihongo Tutor">
        <p className="hint">{authError}</p>
      </Screen>
    );
  }
  if (!user) {
    return (
      <Screen title="Nihongo Tutor">
        <p className="hint">Подключаемся…</p>
      </Screen>
    );
  }

  const isAdmin = (me.data ?? user).role === 'admin';
  return (
    <Screen
      title="Nihongo Tutor"
      tabs={isAdmin ? [{ id: 'home', label: 'Сегодня' }, { id: 'admin', label: 'Админ' }] : undefined}
      activeTab={tab}
      onTab={(id) => setTab(id as Tab)}
    >
      {tab === 'admin' && isAdmin ? <AdminInvites /> : <Home user={me.data ?? user} />}
    </Screen>
  );
}

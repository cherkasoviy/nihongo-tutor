import { useEffect, useState } from 'react';

import { ApiError } from '@/api/client';
import { useMe } from '@/api/hooks';
import { AdminInvites } from '@/features/admin/AdminInvites';
import { KanaGrid } from '@/features/kana/KanaGrid';
import { SessionRunner } from '@/features/session/SessionRunner';
import { Screen } from '@/features/shell/Screen';
import { Progress } from '@/features/stats/Progress';
import { getStartParam } from '@/tg/init';
import { login } from '@/tg/auth';
import { useAuthStore } from '@/store/auth';

type Tab = 'today' | 'kana' | 'progress' | 'admin';

const TABS: { id: Tab; label: string }[] = [
  { id: 'today', label: 'Сегодня' },
  { id: 'kana', label: 'Кана' },
  { id: 'progress', label: 'Прогресс' },
];

function initialTab(): Tab {
  const param = getStartParam();
  if (param === 'admin' || param === 'kana' || param === 'progress' || param === 'today') return param;
  return 'today';
}

export function App() {
  const [authError, setAuthError] = useState<string | null>(null);
  const user = useAuthStore((s) => s.user);
  const [tab, setTab] = useState<Tab>(initialTab);

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
  const tabs = isAdmin ? [...TABS, { id: 'admin' as Tab, label: 'Админ' }] : TABS;

  return (
    <Screen title="Nihongo Tutor" tabs={tabs} activeTab={tab} onTab={(id) => setTab(id as Tab)}>
      {tab === 'today' && <SessionRunner />}
      {tab === 'kana' && <KanaGrid />}
      {tab === 'progress' && <Progress />}
      {tab === 'admin' && isAdmin && <AdminInvites />}
    </Screen>
  );
}

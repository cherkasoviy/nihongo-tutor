import { useState } from 'react';

import type { UserOut } from '@/api/client';
import { useUpdateSettings } from '@/api/hooks';

import styles from './Settings.module.css';

// Evening-weighted, matching the bot's onboarding buttons: a 15-20 minute session of focused
// recall is something most people fit in after the day, not before it.
const REMINDER_HOURS = [8, 12, 18, 20, 21, 22];
const MINUTES = [10, 15, 17, 20, 30];
const FURIGANA: { id: string; label: string }[] = [
  { id: 'always', label: 'всегда' },
  { id: 'auto', label: 'авто' },
  { id: 'off', label: 'выключена' },
];

function detectedTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

/**
 * The reminder is the part of this app that decides whether the habit survives, so it is the first
 * thing on the screen and the only one that can be switched off outright.
 */
export function Settings({ user }: { user: UserOut }) {
  const update = useUpdateSettings();
  const [saved, setSaved] = useState<string | null>(null);

  const save = (body: Parameters<typeof update.mutate>[0], note: string) => {
    update.mutate(body, { onSuccess: () => setSaved(note) });
  };

  const currentHour = user.reminder_time ? Number(user.reminder_time.slice(0, 2)) : null;
  const detected = detectedTimezone();

  return (
    <section>
      <h3 className={styles.heading}>Напоминание</h3>
      <p className="hint">Время по твоему часовому поясу ({user.timezone}).</p>
      <div className={styles.row}>
        {REMINDER_HOURS.map((h) => (
          <button
            key={h}
            type="button"
            className={h === currentHour ? styles.chipActive : styles.chip}
            disabled={update.isPending}
            onClick={() => save({ reminder_time: `${String(h).padStart(2, '0')}:00:00` }, 'Напоминание сохранено')}
          >
            {String(h).padStart(2, '0')}:00
          </button>
        ))}
        <button
          type="button"
          className={currentHour === null ? styles.chipActive : styles.chip}
          disabled={update.isPending}
          onClick={() => save({ clear_reminder: true }, 'Напоминания выключены')}
        >
          Не напоминать
        </button>
      </div>

      <h3 className={styles.heading}>Часовой пояс</h3>
      <p className="hint">Сейчас: {user.timezone}</p>
      {detected && detected !== user.timezone && (
        <button
          type="button"
          className={styles.wide}
          disabled={update.isPending}
          onClick={() => save({ timezone: detected }, 'Часовой пояс обновлён')}
        >
          Переключить на {detected}
        </button>
      )}

      <h3 className={styles.heading}>Сколько заниматься в день</h3>
      <div className={styles.row}>
        {MINUTES.map((m) => (
          <button
            key={m}
            type="button"
            className={m === user.daily_minutes_target ? styles.chipActive : styles.chip}
            disabled={update.isPending}
            onClick={() => save({ daily_minutes_target: m }, 'Цель сохранена')}
          >
            {m} мин
          </button>
        ))}
      </div>

      <h3 className={styles.heading}>Фуригана</h3>
      <p className="hint">Подсказки над кандзи. Пригодится со второго этапа.</p>
      <div className={styles.row}>
        {FURIGANA.map((f) => (
          <button
            key={f.id}
            type="button"
            className={f.id === user.furigana_mode ? styles.chipActive : styles.chip}
            disabled={update.isPending}
            onClick={() => save({ furigana_mode: f.id }, 'Настройка сохранена')}
          >
            {f.label}
          </button>
        ))}
      </div>

      {update.isError && <p className={styles.error}>Не удалось сохранить. Попробуй ещё раз.</p>}
      {saved && !update.isPending && !update.isError && <p className={styles.saved}>{saved}</p>}
    </section>
  );
}

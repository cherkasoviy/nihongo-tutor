import { useStats } from '@/api/hooks';

import styles from './Progress.module.css';

export function Progress() {
  const { data, isLoading, isError } = useStats();

  if (isLoading) return <p className="hint">Считаем прогресс…</p>;
  if (isError || !data) return <p className="hint">Не удалось загрузить прогресс.</p>;

  // Verified only: a syllable someone ticked a box for has not been proven yet, and a headline
  // number that counted it would be the app flattering them.
  const pct = data.kana_total > 0 ? Math.round((data.kana_known / data.kana_total) * 100) : 0;

  return (
    <section>
      <div className={styles.hero}>
        <p className={styles.big}>{pct}%</p>
        <p className="hint">
          каны проверено — {data.kana_known} из {data.kana_total}
        </p>
        {data.kana_claimed > 0 && (
          <p className="hint">
            ещё {data.kana_claimed} отмечено как знакомые — спрошу их в ближайшие дни
          </p>
        )}
      </div>
      <dl className={styles.facts}>
        <dt>Серия</dt>
        <dd>
          {data.streak_current} (рекорд {data.streak_longest})
          {data.freezes_available > 0 && ` · заморозок: ${data.freezes_available}`}
        </dd>
        <dt>К повторению</dt>
        <dd>{data.due_now}</dd>
        <dt>Повторений за неделю</dt>
        <dd>{data.reviews_7d}</dd>
        <dt>Запоминание</dt>
        <dd>{data.retention_7d != null ? `${Math.round(data.retention_7d * 100)}%` : 'пока мало данных'}</dd>
        <dt>Занятий всего</dt>
        <dd>{data.sessions_completed}</dd>
        <dt>Время за неделю</dt>
        <dd>{data.minutes_7d} мин</dd>
      </dl>
    </section>
  );
}

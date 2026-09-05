import type { UserOut } from '@/api/client';

import styles from './Home.module.css';

export function Home({ user }: { user: UserOut }) {
  const name = user.first_name ?? (user.tg_username ? `@${user.tg_username}` : 'друг');
  return (
    <section className={styles.card}>
      <h2 className={styles.greeting}>こんにちは, {name}!</h2>
      <p>
        Занятия появятся здесь на следующем этапе: сначала кана, потом слова и грамматика в живых
        диалогах. Пока всё самое важное происходит в чате с ботом.
      </p>
      <dl className={styles.facts}>
        <dt>Часовой пояс</dt>
        <dd>{user.timezone}</dd>
        <dt>Цель в день</dt>
        <dd>{user.daily_minutes_target} мин</dd>
        <dt>Фуригана</dt>
        <dd>{furiganaLabel(user.furigana_mode)}</dd>
      </dl>
    </section>
  );
}

function furiganaLabel(mode: UserOut['furigana_mode']): string {
  switch (mode) {
    case 'always':
      return 'всегда';
    case 'off':
      return 'выключена';
    default:
      return 'авто (скрывается после успешных повторов)';
  }
}

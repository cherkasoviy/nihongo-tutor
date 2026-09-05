import { useState } from 'react';

import { useCreateInvite, useInvites, useRevokeInvite } from '@/api/hooks';

import styles from './AdminInvites.module.css';

export function AdminInvites() {
  const invites = useInvites(true);
  const create = useCreateInvite();
  const revoke = useRevokeInvite();
  const [maxUses, setMaxUses] = useState(1);
  const [ttlDays, setTtlDays] = useState(14);

  return (
    <section className={styles.wrap}>
      <form
        className={styles.form}
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate({ max_uses: maxUses, ttl_days: ttlDays > 0 ? ttlDays : null });
        }}
      >
        <label>
          Использований
          <input type="number" min={1} max={100} value={maxUses} onChange={(e) => setMaxUses(Number(e.target.value))} />
        </label>
        <label>
          Дней действия (0 = бессрочно)
          <input type="number" min={0} max={365} value={ttlDays} onChange={(e) => setTtlDays(Number(e.target.value))} />
        </label>
        <button type="submit" disabled={create.isPending}>
          Создать приглашение
        </button>
        {create.isError && <p className="hint">Не удалось создать приглашение.</p>}
      </form>

      {invites.isLoading && <p className="hint">Загружаем…</p>}
      {invites.isError && <p className="hint">Не удалось загрузить приглашения.</p>}
      {invites.data && invites.data.length === 0 && <p className="hint">Приглашений пока нет.</p>}
      <ul className={styles.list}>
        {invites.data?.map((inv) => (
          <li key={inv.id} className={inv.revoked ? styles.itemRevoked : styles.item}>
            <div>
              <code>{inv.code}</code> · {inv.uses}/{inv.max_uses}
              {inv.revoked && ' · отозвано'}
              {inv.expires_at && ` · до ${new Date(inv.expires_at).toLocaleDateString('ru-RU')}`}
            </div>
            {inv.start_link && (
              <a href={inv.start_link} target="_blank" rel="noreferrer">
                {inv.start_link}
              </a>
            )}
            {!inv.revoked && (
              <button type="button" onClick={() => revoke.mutate(inv.id)} disabled={revoke.isPending}>
                Отозвать
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

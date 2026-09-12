import { useMemo, useState } from 'react';

import type { KanaCell, KanaScript } from '@/api/client';
import { useKanaGrid, useSetKanaKnown } from '@/api/hooks';
import { StrokeOrder } from '@/features/kana/StrokeOrder';

import styles from './KanaGrid.module.css';

const SCRIPTS: { id: KanaScript; label: string }[] = [
  { id: 'hiragana', label: 'Хирагана' },
  { id: 'katakana', label: 'Катакана' },
];

const KIND_LABELS: Record<string, string> = {
  basic: 'Основные',
  dakuten: 'Звонкие',
  handakuten: 'Полузвонкие',
  yoon: 'Слитные',
};

/**
 * The learner's map of the syllabary. Strength is FSRS retrievability, not a review count: a
 * character drilled ten times last month is weaker than one drilled twice yesterday, and the grid
 * should say so.
 */
export function KanaGrid() {
  const [script, setScript] = useState<KanaScript>('hiragana');
  const [selected, setSelected] = useState<KanaCell | null>(null);
  const [placing, setPlacing] = useState(false);
  const { data, isLoading, isError } = useKanaGrid(script);
  const setKnown = useSetKanaKnown();

  const groups = useMemo(() => {
    const byKind = new Map<string, KanaCell[]>();
    for (const cell of data ?? []) {
      const list = byKind.get(cell.kind) ?? [];
      list.push(cell);
      byKind.set(cell.kind, list);
    }
    return [...byKind.entries()];
  }, [data]);

  if (isLoading) return <p className="hint">Загружаем кану…</p>;
  if (isError) return <p className="hint">Не удалось загрузить кану. Попробуй ещё раз.</p>;

  return (
    <section>
      <nav className={styles.scripts} aria-label="Азбука">
        {SCRIPTS.map((s) => (
          <button
            key={s.id}
            type="button"
            className={s.id === script ? styles.scriptActive : styles.script}
            onClick={() => {
              setScript(s.id);
              setSelected(null);
            }}
          >
            {s.label}
          </button>
        ))}
      </nav>

      <div className={styles.placeBar}>
        <button
          type="button"
          className={placing ? styles.scriptActive : styles.script}
          onClick={() => {
            setPlacing((on) => !on);
            setSelected(null);
          }}
        >
          {placing ? 'Готово' : 'Уже знаю…'}
        </button>
      </div>

      {placing && (
        <p className="hint">
          Отметь знаки, которые уже читаешь — я не буду их объяснять заново, но всё равно спрошу их
          в ближайшие пару недель, чтобы проверить. Нажми на группу целиком или на отдельный знак.
        </p>
      )}

      {groups.map(([kind, cells]) => (
        <div key={kind} className={styles.group}>
          <div className={styles.groupHead}>
            <h3 className={styles.groupTitle}>{KIND_LABELS[kind] ?? kind}</h3>
            {placing && (
              <button
                type="button"
                className={styles.groupClaim}
                disabled={setKnown.isPending}
                onClick={() =>
                  setKnown.mutate({
                    itemIds: cells.filter((c) => !c.introduced).map((c) => c.item_id),
                    known: true,
                  })
                }
              >
                Знаю все
              </button>
            )}
          </div>
          <div className={styles.grid}>
            {cells.map((cell) => (
              <button
                key={cell.item_id}
                type="button"
                className={styles.cell}
                data-state={strength(cell)}
                onClick={() =>
                  placing
                    ? setKnown.mutate({ itemIds: [cell.item_id], known: !cell.introduced })
                    : setSelected(cell)
                }
                aria-label={`${cell.char} — ${cell.cyrillic}`}
              >
                <span className={styles.char}>{cell.char}</span>
                <span className={styles.reading}>{cell.cyrillic}</span>
              </button>
            ))}
          </div>
        </div>
      ))}

      {selected && <KanaDetail cell={selected} onClose={() => setSelected(null)} />}
    </section>
  );
}

/** Four buckets, because a continuous gradient reads as noise at this cell size. */
function strength(cell: KanaCell): 'new' | 'weak' | 'ok' | 'strong' {
  if (!cell.introduced) return 'new';
  const r = cell.retrievability ?? 0;
  if (r < 0.6) return 'weak';
  if (r < 0.9) return 'ok';
  return 'strong';
}

function KanaDetail({ cell, onClose }: { cell: KanaCell; onClose: () => void }) {
  return (
    <div className={styles.sheet} role="dialog" aria-label={`Знак ${cell.char}`}>
      <button type="button" className={styles.close} onClick={onClose} aria-label="Закрыть">
        ✕
      </button>
      <div className={styles.detailHead}>
        <StrokeOrder char={cell.char} />
        <div>
          <p className={styles.detailChar}>{cell.char}</p>
          <p className={styles.detailReading}>{cell.cyrillic}</p>
        </div>
      </div>
      {cell.mnemonic_ru && <p className={styles.mnemonic}>{cell.mnemonic_ru}</p>}
      {cell.example_word && (
        <p className={styles.example}>
          <b>{cell.example_word}</b>
          {cell.example_reading && cell.example_reading !== cell.example_word && ` (${cell.example_reading})`}
          {cell.example_gloss_ru && ` — ${cell.example_gloss_ru}`}
        </p>
      )}
      <p className="hint">
        {cell.introduced
          ? `Повторов: ${cell.reps}${cell.retrievability != null ? `, вероятность вспомнить сейчас ${Math.round(cell.retrievability * 100)}%` : ''}`
          : 'Ещё не проходили'}
      </p>
    </div>
  );
}

import { useEffect, useState } from 'react';

import type { AnswerResult, SessionStep } from '@/api/client';
import { useAnswerStep, useStartSession } from '@/api/hooks';

import styles from './SessionRunner.module.css';

/**
 * Today's lesson, one step at a time.
 *
 * The server owns every rule — which step is next, how an answer grades, when the session ends —
 * so this component only renders and forwards taps. That is what lets a learner answer half the
 * session in the chat and the rest here without the two disagreeing.
 */
export function SessionRunner() {
  const start = useStartSession();
  const answer = useAnswerStep();
  const [step, setStep] = useState<SessionStep | null>(null);
  const [feedback, setFeedback] = useState<AnswerResult | null>(null);
  const [finished, setFinished] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });

  const begin = start.mutate;
  useEffect(() => {
    begin(undefined, {
      onSuccess: (s) => {
        setStep(s.current);
        setProgress({ done: s.completed_steps, total: s.planned_steps });
        setFinished(s.current === null);
      },
    });
  }, [begin]);

  if (start.isPending) return <p className="hint">Готовим занятие…</p>;
  if (start.isError) return <p className="hint">Не удалось загрузить занятие. Попробуй ещё раз.</p>;

  if (finished) {
    return (
      <section className={styles.card}>
        <h2 className={styles.done}>Занятие сделано 🎉</h2>
        <p className="hint">
          {progress.total > 0
            ? `Пройдено шагов: ${progress.done} из ${progress.total}.`
            : 'На сегодня всё — новых знаков пока нет и повторять нечего.'}
        </p>
      </section>
    );
  }

  if (!step) return <p className="hint">Загружаем шаг…</p>;

  const submit = (body: { choice?: number; acknowledged?: boolean }) => {
    if (answer.isPending || feedback) return;
    answer.mutate(
      { stepId: step.id, body },
      {
        onSuccess: (result) => {
          if (!result.accepted) {
            // Already answered elsewhere (the bot, or a double tap): just move on.
            setFeedback(null);
            setStep(result.next);
            setFinished(result.session_finished);
            return;
          }
          setProgress((p) => ({ ...p, done: p.done + 1 }));
          if (body.acknowledged) {
            setStep(result.next);
            setFinished(result.session_finished);
            return;
          }
          setFeedback(result);
          window.setTimeout(() => {
            setFeedback(null);
            setStep(result.next);
            setFinished(result.session_finished);
          }, result.correct ? 700 : 1800);
        },
      },
    );
  };

  return (
    <section className={styles.card}>
      <Progress done={progress.done} total={progress.total} />
      {step.mode === 'ack' ? (
        <Intro step={step} onNext={() => submit({ acknowledged: true })} />
      ) : (
        <Choice step={step} feedback={feedback} onPick={(i) => submit({ choice: i })} />
      )}
    </section>
  );
}

function Progress({ done, total }: { done: number; total: number }) {
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  return (
    <div className={styles.progress} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className={styles.progressFill} style={{ width: `${pct}%` }} />
    </div>
  );
}

function Intro({ step, onNext }: { step: SessionStep; onNext: () => void }) {
  return (
    <>
      <p className={styles.bigChar}>{step.char}</p>
      <p className={styles.reading}>{step.cyrillic}</p>
      {step.mnemonic_ru && <p className={styles.mnemonic}>{step.mnemonic_ru}</p>}
      {step.example_word && (
        <p className={styles.example}>
          <b>{step.example_word}</b>
          {step.example_gloss_ru && ` — ${step.example_gloss_ru}`}
        </p>
      )}
      <button type="button" className={styles.next} onClick={onNext}>
        Запомнила
      </button>
    </>
  );
}

function Choice({
  step,
  feedback,
  onPick,
}: {
  step: SessionStep;
  feedback: AnswerResult | null;
  onPick: (index: number) => void;
}) {
  const asksForGlyph = step.kind === 'review_prod';
  return (
    <>
      <p className={styles.question}>{asksForGlyph ? 'Какой это знак?' : 'Как читается?'}</p>
      <p className={asksForGlyph ? styles.readingPrompt : styles.bigChar}>
        {asksForGlyph ? step.cyrillic : step.char}
      </p>
      <div className={styles.choices}>
        {step.choices.map((label, i) => (
          <button
            key={`${label}-${i}`}
            type="button"
            className={asksForGlyph ? styles.choiceGlyph : styles.choice}
            disabled={feedback !== null}
            onClick={() => onPick(i)}
          >
            {label}
          </button>
        ))}
      </div>
      {feedback && (
        <p className={feedback.correct ? styles.correct : styles.wrong}>
          {feedback.correct ? 'Верно ✓' : `Правильный ответ: ${feedback.correct_label}`}
        </p>
      )}
    </>
  );
}

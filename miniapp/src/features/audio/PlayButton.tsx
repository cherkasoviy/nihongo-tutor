import { useEffect, useRef, useState } from 'react';

import { fetchAudioUrl } from '@/api/client';
import { ensureToken } from '@/tg/auth';

import styles from './PlayButton.module.css';

/**
 * Speak a syllable.
 *
 * **The clip is fetched on mount, not on tap.** Safari ties `play()` to a user gesture, and whether
 * that activation survives an `await` on a network request is version-dependent — so fetching
 * inside the handler risks her *first* tap silently doing nothing and the second working, which is
 * the worst shape of bug: it looks like she mistapped. Prefetching makes the handler synchronous,
 * so the gesture is never in doubt.
 *
 * The cost is one request per card shown rather than per card tapped. That is fine here: this
 * renders on the detail sheet and the intro step, one syllable at a time — never across the grid.
 *
 * The object URL is revoked on unmount; a blob that outlives its component is a leak the browser
 * cannot collect on its own.
 */
export function PlayButton({ itemId, label }: { itemId: string; label: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    let live = true;
    let created: string | null = null;
    setUrl(null);
    setFailed(false);

    void (async () => {
      try {
        const src = await fetchAudioUrl(`/api/audio/kana/${itemId}.mp3`, await ensureToken());
        if (!live) {
          URL.revokeObjectURL(src); // unmounted mid-flight
          return;
        }
        created = src;
        setUrl(src);
      } catch {
        if (live) setFailed(true);
      }
    })();

    return () => {
      live = false;
      if (created) URL.revokeObjectURL(created);
    };
  }, [itemId]);

  const play = () => {
    if (!url) return;
    audio.current ??= new Audio();
    audio.current.src = url;
    // Still guarded: a refused play is possible on iOS if the gesture is not trusted, and it must
    // surface as a muted icon rather than an unhandled rejection.
    void audio.current.play().catch(() => setFailed(true));
  };

  return (
    <button
      type="button"
      className={styles.play}
      onClick={play}
      disabled={!url && !failed}
      aria-label={`Произношение: ${label}`}
    >
      {failed ? '🔇' : url ? '🔊' : '…'}
    </button>
  );
}

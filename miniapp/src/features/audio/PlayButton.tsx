import { useEffect, useRef, useState } from 'react';

import { fetchAudioUrl } from '@/api/client';
import { ensureToken } from '@/tg/auth';

import styles from './PlayButton.module.css';

/**
 * Speak a syllable.
 *
 * The clip is fetched once and kept as an object URL for the life of the component, so tapping
 * repeatedly — which is exactly what someone drilling a sound does — costs one request, not one per
 * tap. The URL is revoked on unmount; a blob that outlives its component is a leak the browser
 * cannot collect on its own.
 */
export function PlayButton({ itemId, label }: { itemId: string; label: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [url]);

  const play = async () => {
    setFailed(false);
    let src = url;
    if (!src) {
      setLoading(true);
      try {
        src = await fetchAudioUrl(`/api/audio/kana/${itemId}.mp3`, await ensureToken());
        setUrl(src);
      } catch {
        setFailed(true);
        return;
      } finally {
        setLoading(false);
      }
    }
    audio.current ??= new Audio();
    audio.current.src = src;
    // A refused play is normal on iOS when the gesture is not trusted; it must not throw.
    void audio.current.play().catch(() => setFailed(true));
  };

  return (
    <button
      type="button"
      className={styles.play}
      onClick={play}
      disabled={loading}
      aria-label={`Произношение: ${label}`}
    >
      {loading ? '…' : failed ? '🔇' : '🔊'}
    </button>
  );
}

import { useEffect, useState } from 'react';

import styles from './KanaGrid.module.css';

/**
 * Stroke order from KanjiVG (CC BY-SA 3.0), loaded lazily by codepoint.
 *
 * The dataset is not vendored in the repo — it is a separate download (`make fetch-kanjivg`) with
 * its own licence and attribution. When it is absent the component simply renders nothing extra, so
 * a fresh checkout still shows a complete, working kana grid.
 */
export function StrokeOrder({ char }: { char: string }) {
  const [svg, setSvg] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSvg(null);
    const codepoint = char.codePointAt(0);
    if (codepoint === undefined) return;

    fetch(`/kanjivg/${codepoint.toString(16).padStart(5, '0')}.svg`)
      .then((res) => (res.ok ? res.text() : null))
      .then((text) => {
        // A dev server that rewrites unknown paths to index.html would otherwise inject HTML here.
        if (!cancelled && text && text.trimStart().startsWith('<svg')) setSvg(text);
      })
      .catch(() => {
        /* offline or not downloaded: the grid works without it */
      });
    return () => {
      cancelled = true;
    };
  }, [char]);

  if (!svg) return null;
  // KanjiVG ships plain path data with no scripting; it is static repo content, not user input.
  return <div className={styles.stroke} aria-hidden dangerouslySetInnerHTML={{ __html: svg }} />;
}

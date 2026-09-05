/**
 * Telegram Mini App bootstrap via @telegram-apps/sdk-react.
 *
 * Outside Telegram (plain browser) the SDK throws on init; we surface that instead of crashing so
 * the page can still be opened for a sanity check.
 */
import {
  init,
  isTMA,
  miniApp,
  retrieveRawInitData,
  retrieveLaunchParams,
  themeParams,
  viewport,
} from '@telegram-apps/sdk-react';

export type TgInitResult = { ok: true } | { ok: false; reason: string };

let rawInitData: string | undefined;
let startParam: string | undefined;

export function initTelegram(): TgInitResult {
  try {
    if (!isTMA()) {
      return { ok: false, reason: 'Страница открыта не внутри Telegram.' };
    }
    init();
    const launch = retrieveLaunchParams();
    startParam = launch.tgWebAppStartParam ?? undefined;
    rawInitData = retrieveRawInitData();

    if (miniApp.mountSync.isAvailable()) miniApp.mountSync();
    if (themeParams.mountSync.isAvailable()) themeParams.mountSync();
    if (themeParams.bindCssVars.isAvailable()) themeParams.bindCssVars();
    if (viewport.mount.isAvailable()) {
      void viewport.mount().then(() => {
        if (viewport.bindCssVars.isAvailable()) viewport.bindCssVars();
        if (viewport.expand.isAvailable()) viewport.expand();
      });
    }
    if (miniApp.ready.isAvailable()) miniApp.ready();
    return { ok: true };
  } catch (err) {
    return { ok: false, reason: err instanceof Error ? err.message : String(err) };
  }
}

export function getRawInitData(): string | undefined {
  return rawInitData;
}

export function getStartParam(): string | undefined {
  return startParam;
}

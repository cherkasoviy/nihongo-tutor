import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { App } from '@/App';
import { initTelegram } from '@/tg/init';
import { NotInTelegram } from '@/features/shell/NotInTelegram';
import '@/styles/global.css';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30_000, refetchOnWindowFocus: false } },
});

const root = createRoot(document.getElementById('root')!);
const tg = initTelegram();

root.render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>{tg.ok ? <App /> : <NotInTelegram reason={tg.reason} />}</QueryClientProvider>
  </StrictMode>,
);

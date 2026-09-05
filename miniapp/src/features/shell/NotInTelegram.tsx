export function NotInTelegram({ reason }: { reason: string }) {
  return (
    <div style={{ padding: 24, fontFamily: 'system-ui, sans-serif' }}>
      <h1 style={{ fontSize: '1.3rem' }}>Nihongo Tutor</h1>
      <p>Это мини-приложение Telegram. Открой его из чата с ботом.</p>
      <p style={{ opacity: 0.6, fontSize: '0.85rem' }}>{reason}</p>
    </div>
  );
}

import type { ReactNode } from 'react';

import styles from './Screen.module.css';

interface Tab {
  id: string;
  label: string;
}

interface Props {
  title: string;
  tabs?: Tab[];
  activeTab?: string;
  onTab?: (id: string) => void;
  children: ReactNode;
}

export function Screen({ title, tabs, activeTab, onTab, children }: Props) {
  return (
    <div className={styles.screen}>
      <header className={styles.header}>
        <h1 className={styles.title}>{title}</h1>
        {tabs && (
          <nav className={styles.tabs} aria-label="Разделы">
            {tabs.map((t) => (
              <button
                key={t.id}
                type="button"
                className={t.id === activeTab ? styles.tabActive : styles.tab}
                onClick={() => onTab?.(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>
        )}
      </header>
      <main className={styles.main}>{children}</main>
    </div>
  );
}

# Content and audio pipeline

Content is an **asset in git**, never something that lives only in a database. The database is
disposable: drop it, recreate it, deploy to a new host, and every syllable, word and sentence comes
back from `backend/data/seed/*.json` unchanged. Learner data is the only thing a backup protects.

```
backend/data/seed/*.json   ──import──▶   items + kana + vocab + …      (any database)
        ▲                                          │
        └──────────────── export ──────────────────┘
```

Both directions must exist. Import alone means the day someone edits a mnemonic directly in the
database, that edit lives in exactly one place — which is the failure this design exists to prevent.

## Rules

1. **Natural keys, never surrogate ones.** `kana` resolves on `(script, char)`, `items` on
   `(type, ref_id)`, vocabulary on `slug`. Re-importing upserts; it never duplicates rows or
   renumbers the curriculum. `import_kana.py` is the reference implementation — follow it.
2. **Never seed content through Alembic.** Migrations are schema, run once, and cannot be corrected
   in place. Content changes on its own cadence and must be re-runnable.
3. **Provenance and a review gate on every entry.** `source` records where the text came from
   (`hand`, `claude-session`, `claude:<model>`); `reviewed` records whether a human has read it.
   The importer sets `items.active = true` only for `reviewed: true`, with `--include-unreviewed`
   for local development. For a language tutor this is not bureaucracy: the learner cannot detect a
   subtly wrong Japanese sentence, so she will simply learn it.
4. **`export-*` is the audit.** After `export`, `git diff` empty means the database and the repo
   agree. A non-empty diff is either an edit to commit or drift to explain.

## Commands to build

| Command | Does |
|---|---|
| `nihongo-content import-kana` | exists |
| `nihongo-content import-vocab` | upsert `vocab_core.json` on `slug`, plus its `items` hub rows |
| `nihongo-content import-all` | every seed file, in curriculum order |
| `nihongo-content export-kana` / `export-vocab` | database → seed JSON, keys sorted, stable formatting |
| `nihongo-content check` | validate every seed file against its pydantic model and exit non-zero on failure — run it in CI so a malformed seed can never reach a database |
| `nihongo-content warm-audio` | pre-synthesize every clip referenced by the seeds |

## Audio

Every syllable and every example gets audio. The budget question is settled — measured against the
current seeds:

| | clips | characters |
|---|---|---|
| Kana syllables | 208 | 274 |
| Their example words | 208 | 708 |
| Vocabulary | 93 | 293 |
| Their example sentences | 93 | 990 |
| **Total, one encoding** | **602** | **2,265** |
| **Total, both encodings** | **1,204** | **4,530** |

That is **0.45% of the 1M-character monthly Neural2 free tier**. Audio on everything, regenerated
from scratch every single month, is free. Do not ration it, do not add it lazily "where it matters
most" — synthesize the lot.

### Synthesize the reading, never the written form

Send `reading` (pure kana) to the API, never `word`. `日本` is にほん or にっぽん depending on
context, `何` is なに or なん, and a TTS engine guessing wrong produces confidently wrong audio —
the exact failure mode a beginner cannot catch. The reading field is unambiguous by construction.

Example sentences are the exception worth understanding: send them as written, because the engine's
sentence analysis is what makes は read as *wa* when it is a particle and *ha* when it is a syllable.
An isolated は on a kana card is correctly read *ha*; the same character inside
`これはなんですか` is correctly read *wa*. Splitting sentences into per-word synthesis would break
that, so don't.

### Two encodings, deliberately

- **MP3** for the Mini App. iOS Safari and the WebView Telegram uses do not reliably play Ogg/Opus;
  audio that works on Android and silently fails on an iPhone is a bug you will not reproduce.
- **OGG_OPUS** for Telegram voice messages, which is what the Bot API wants natively.

Both come from the same text at 2,265 characters each. Generate both.

### Content-addressed, derived at read time

```
key    = sha256(f"{text}|{voice}|{speaking_rate}|{encoding}")[:16]
path   = backend/data/audio/{key}.{mp3|ogg}
```

No `audio_id` column, no foreign key, no migration: the caller hashes the text it is about to show
and looks for the file. That makes the cache self-invalidating — change a voice, a rate, or a single
character of the text and the key changes, so stale audio is impossible by construction rather than
by discipline.

- **Pin the voice** in settings (`TTS_VOICE`, e.g. a `ja-JP-Neural2` voice — list what your project
  actually has with the voices endpoint and record the exact id). It is part of the key, so changing
  it regenerates rather than corrupts.
- **Lazy synthesis with a warm command.** A missing file is synthesized on demand and cached; then
  `warm-audio` pre-generates everything from the seeds so no learner is ever the one who waits.
- **`backend/data/audio/` is git-ignored.** It is derived, and `backup.sh` already tarballs it.
- **Guard the quota.** Count characters per month in the usage ledger and refuse to synthesize past
  a configured ceiling. At 0.45% of the free tier the only way to exceed it is a bug — which is
  exactly why the guard is worth having.

## Seed file shape

`vocab_core.json`, tranche 1, 93 entries ordered so that everything usable in the first sittings
after kana comes first:

```json
{
  "slug": "vocab:mizu",
  "word": "水",
  "reading": "みず",
  "gloss_ru": "вода",
  "pos": "noun",
  "curriculum_order": 26,
  "tags": ["food"],
  "example": { "ja": "みずをください。", "reading": "みずをください。", "gloss_ru": "Дайте, пожалуйста, воды." },
  "source": "claude-session",
  "reviewed": false
}
```

`word` carries kanji where that is the ordinary written form; `reading` is always pure kana and is
what furigana and audio use. No romaji anywhere, per the plan. Everything ships `reviewed: false` —
the owner reads it and flips the flag, which is the point of the gate.

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
3. **Provenance and a review gate on every entry.** The field names come from `vocab_lemmas` in
   [`PLAN.md`](PLAN.md): `gloss_source` records where the text came from (`jmdict_rus`, `warodai`,
   `claude`, `human`) and `gloss_review_status` records how far it has got (`needs_review`,
   `approved`, `rejected`). The importer sets `items.active = true` only for `approved`, with
   `--include-unreviewed` for local development. For a language tutor this is not bureaucracy: a
   learner cannot detect a subtly wrong Japanese sentence, so they will simply learn it.
4. **`export-*` is the audit.** After `export`, `git diff` empty means the database and the repo
   agree. A non-empty diff is either an edit to commit or drift to explain.

## Commands

| Command | Does | |
|---|---|---|
| `import-kana` | upsert the two kana seeds on `(script, char)`, plus their `items` rows | built |
| `import-vocab` | upsert `vocab_core.json` on `slug`, plus its `items` hub rows. `--include-unreviewed` activates entries that are not yet approved — local development only | built |
| `import-all` | every seed file, in curriculum order. This is what `deploy.sh` runs | built |
| `export-vocab` | database → `vocab_core.json`. `--out` writes elsewhere | built |
| `export-kana` | database → the kana seeds | **not built** — the remaining gap in rule 4 |
| `import-pairs` | upsert `listening_pairs.json` on `slug` | **not built** — lands with `listen_choose`, and joins `import-all` then |
| `check` | validate every seed file against its pydantic model, exit non-zero on failure. Needs no database, and CI runs it before the Postgres steps so a malformed seed can never reach one. `--seed-dir` validates a candidate file in place | built |
| `doctor` | the database-connectivity probe `check` used to be | built |
| `warm-audio` | pre-synthesize every clip the seeds imply: readings for isolated items, sentences as written | built |

Field order in an export is the **authored** order, not alphabetical. Alphabetical would sort
`curriculum_order` above `word` and turn every export into a four-thousand-line diff, which
destroys the only thing rule 4 is for. `version`, `kind` and the prose `note` are carried over from
the file on disk: they describe the file rather than any row, and no column holds them.

One thing to know before writing `import-pairs`: `listening_pairs.json` does not round-trip
today, because `pair:saka-sakka` lists `gloss_source` before `note_ru` while the other 21 pairs
put it after. Normalise that key order in the same PR, or the first export will produce a diff
that looks like drift and is not.

## Audio

Every syllable and every example gets audio. The budget question is settled — measured against the
current seeds, counting what actually goes to the API (readings for isolated items, sentences as
written — see below):

| | clips | characters |
|---|---|---|
| Kana syllables | 208 | 274 |
| Their example words | 206 | 701 |
| Vocabulary readings | 220 | 686 |
| Their example sentences | 217 | 1,982 |
| **Total, one encoding, deduplicated** | **803** | **3,515** |
| **Total, both encodings** | **1,606** | **7,030** |

Those are the numbers `warm_audio.seed_texts()` actually produces, not a sum of the rows: several
kana share an example word, three sentences repeat, and a vocabulary reading can equal a kana
example, so the deduplicated set is smaller than the parts.

That is **0.70% of the 1M-character monthly Neural2 free tier**. Audio on everything, regenerated
from scratch every single month, is free. Do not ration it, do not add it lazily "where it matters
most" — synthesize the lot. Adding the slow variant doubles it to **1.41%**, which is the first
number here that is no longer negligible — still trivially affordable, but it is now worth
generating the slow variant for what the learner actually replays rather than for all 803 clips.

### Synthesize the reading, never the written form

Send `reading` (pure kana) to the API for anything isolated — a syllable, a vocabulary headword —
never `word`. 日本 is にほん or にっぽん depending on context, 何 is なに or なん, and a TTS engine
guessing wrong produces confidently wrong audio: the exact failure mode a beginner cannot catch.
The reading field is unambiguous by construction.

This is not hypothetical. Running Sudachi over this tranche's 93 example sentences reproduces the
pinned reading on 86 of them and disagrees on seven — 明日 as あす not あした, 私 as わたくし not
わたし, 日本 as にっぽん, 四人 as よんにん, 何時 as なんどき. Every one of those is a legitimate
reading of the characters and the wrong one for a beginner's course. That is the whole argument for
keeping `reading` as authored data rather than deriving it, and it is what [`PLAN.md`](PLAN.md)
means by pinning canonical readings.

Example sentences are the exception worth understanding: send them **as written**, because the
engine's sentence analysis is what makes は read as *wa* when it is a particle and *ha* when it is a
syllable. An isolated は on a kana card is correctly read *ha*; the same character inside
`これは何ですか` is correctly read *wa*. Splitting sentences into per-word synthesis would break
that, so don't. `example.reading` is still authored for every sentence — furigana and the review
queue need it even where the synthesiser does not.

### Two encodings, deliberately

- **MP3** for the Mini App. iOS Safari and the WebView Telegram uses do not reliably play Ogg/Opus;
  audio that works on Android and silently fails on an iPhone is a bug you will not reproduce.
- **OGG_OPUS** for Telegram voice messages, which is what the Bot API wants natively.

Both come from the same text. Generate both.

### Content-addressed — which is what `audio_assets` already is

[`PLAN.md`](PLAN.md) specifies the table and the Phase 0 baseline migration already created it:

```
audio_assets: hash = sha256(provider|voice|rate|ssml_version|text) unique,
              ogg_path, mp3_path, duration_ms, timepoints, tg_file_id
```

That `hash` **is** the content-addressed key. Nothing here replaces the table; the rules below are
how to use it.

- **Hash the full input, not a subset.** `provider` and `ssml_version` belong in the key alongside
  voice, rate and text. Drop `provider` and a future move between engines silently serves the old
  vendor's audio; drop `ssml_version` and the normal and slow variants — the slow one is an SSML
  `prosody rate` wrapper, per the plan — collide on the same key. Both are the same failure the
  hash exists to prevent, so the key has to cover everything that changes the bytes.
- **One row, both encodings.** `ogg_path` and `mp3_path` hang off the same row: same text, same
  voice, same rate, two containers. Two rows would let them drift.
- **Keep `tg_file_id`.** After the first upload Telegram will re-send a clip by file id instead of
  taking the bytes again. A filesystem-only cache throws that away on every single send, which is
  the one place this design would actually cost something measurable.
- **Keep `timepoints`.** The plan wants word timepoints from the TTS response; they are what lets a
  sentence highlight in time with the audio. They are returned once, at synthesis, and cannot be
  recovered from the file afterwards.
- **Files stay content-addressed on disk**: `backend/data/audio/{hash}.{mp3,ogg}`, with the row
  pointing at them. The cache is self-invalidating — change a voice, a rate, or a single character
  and the key changes, so stale audio is impossible by construction rather than by discipline.
- **Pin the voice** in settings (`TTS_VOICE`, e.g. a `ja-JP-Neural2` voice — list what the project
  actually has with the voices endpoint and record the exact id). It is part of the key, so changing
  it regenerates rather than corrupts.
- **Lazy synthesis with a warm command.** A missing file is synthesized on demand and cached; then
  `warm-audio` pre-generates everything from the seeds so no learner is ever the one who waits.
- **`backend/data/audio/` is git-ignored.** It is derived, and `backup.sh` already tarballs it.
- **Guard the quota.** Count characters per month in `ai_usage_ledger` and refuse to synthesize past
  a configured ceiling. At under half a percent of the free tier the only way to exceed it is a bug
  — which is exactly why the guard is worth having.

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
  "example": { "ja": "水をください。", "reading": "みずをください。", "gloss_ru": "Дайте, пожалуйста, воды." },
  "gloss_source": "claude",
  "gloss_review_status": "needs_review"
}
```

`word` and `example.ja` carry kanji wherever ordinary written Japanese does — 69 of the 93 headwords
and 89 of the 93 sentences; the four without are the ones whose headword is kana or katakana
(`ありがとうございます`, `はい`, `トイレ`, `いくら`). `reading` and `example.reading` are always pure
kana and are what furigana and audio use. No romaji anywhere, per the plan.

Kanji in the examples is what makes the furigana toggle mean something: the plan puts kanji in front
of the learner from the first vocabulary item with furigana on by default, and a kana-only sentence
gives that feature nothing to render. It is also why `example.reading` is a separate field rather
than a copy — before this tranche was rewritten it was byte-identical to `example.ja` in all 93 rows.

**This is the `core` stage, not the kana stage.** The plan's validation gate requires kana-only text
during the kana bootcamp, and `backend/data/seed/kana_*.json` stays that way. Kanji is correct here
and wrong there; do not "fix" the kana seeds to match.

Everything ships `gloss_review_status: "needs_review"` — the owner reads it and promotes it to
`approved`, which is the point of the gate.

### Tranche 2

`vocab_core.json` now continues to `curriculum_order` 220. Tranche 2 (94 onward, 127 entries) adds
demonstratives, everyday adverbs and set phrases, numbers one to ten plus 百/千/円, the days of the
week, weather and nature, colours, home and personal objects, a second batch of verbs, body parts,
places of work and the na-adjectives (`pos: adjective` with tag `na-adjective`, since the conjugation
class is what the grammar stage will need, not a separate part of speech). New `pos` values are
`adverb`, `numeral` and `determiner`.

Ordering within the tranche is approximate i+1: an example sentence uses tranche-1 words and earlier
tranche-2 words wherever possible, so the first time a learner sees 会社 the verb 働く in its
sentence is already known. The audio budget grows by 3,104 characters for both encodings, to about
0.73% of the free tier in total.

Where a number's reading changes inside a compound, the example pins the compound: 四時 is よじ, 七時
is しちじ, 九時 is くじ. That is the same argument as the Sudachi paragraph above, made by the data.

### What this tranche does not have yet

`vocab_lemmas` in [`PLAN.md`](PLAN.md) also carries `jlpt_level`, `freq_rank`, `pitch_pattern`,
`jmdict_seq` and `kanji_forms[]`. This tranche is hand-authored and has none of them: it exists so
the first sittings after kana are usable without waiting for the JMdict/Warodai/BCCWJ pipeline.

When `build_vocab` lands, **these 93 are the canonical head of the curriculum and the pipeline
merges onto them** — matching on `slug`, filling the empty columns, and leaving `curriculum_order`,
`gloss_ru` and the examples alone. The alternative, letting frequency ranking re-derive the opening
of the course, would replace an order chosen for what a beginner can immediately say with one
chosen by corpus frequency, and would throw away glosses and sentences a human has reviewed. The
pipeline's job is to extend this list and enrich it, not to regenerate it.

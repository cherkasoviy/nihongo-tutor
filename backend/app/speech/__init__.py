"""Text to speech, and later speech to text.

The design is in ``docs/CONTENT.md`` and this package implements it rather than re-deciding it.
Two things are load-bearing and easy to undo by accident:

* **Send the reading for anything isolated, the sentence as written for a sentence.** 日本 is
  にほん or にっぽん, 何 is なに or なん, and an engine guessing wrong produces confidently wrong
  audio — the one failure a beginner cannot catch. Sentences are the exception: the engine's own
  analysis is what makes は read *wa* as a particle and *ha* as a syllable, and splitting a sentence
  into per-word synthesis destroys that.
* **The cache key covers everything that changes the bytes**, including the provider and the SSML
  template. Drop the provider and a future engine swap silently serves the old vendor's audio; drop
  the SSML version and the normal and slow readings of the same text collide on one key.
"""

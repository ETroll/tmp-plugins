---
name: word-count
description: Count words, lines, and characters in a block of text or a file, using the bundled script. Use when the user asks for a word count, line count, or basic text statistics.
---

# Word count

Use the bundled script to compute statistics for a piece of text.

1. Write the text to a temporary file, or use an existing file path the user gave you.
2. Run `scripts/count_words.py <path>`.
3. Report the word count, line count, and character count from its JSON output.

Example:

```bash
python3 scripts/count_words.py notes.txt
```

```json
{"lines": 12, "words": 143, "characters": 812}
```

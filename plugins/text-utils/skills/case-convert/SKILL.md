---
name: case-convert
description: Convert identifiers or phrases between snake_case, kebab-case, camelCase, PascalCase, and Title Case. Use when the user asks to rename something to a different case convention.
---

# Case convert

Convert the given text between common case conventions.

1. Split the input into words, treating spaces, hyphens, underscores, and
   camel/Pascal humps as word boundaries.
2. Rejoin the words in the requested convention:
   - `snake_case`: lowercase words joined with `_`
   - `kebab-case`: lowercase words joined with `-`
   - `camelCase`: first word lowercase, remaining words capitalized, no separator
   - `PascalCase`: every word capitalized, no separator
   - `Title Case`: every word capitalized, joined with a space
3. Return the converted string.

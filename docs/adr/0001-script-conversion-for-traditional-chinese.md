# Read Traditional Chinese via Script Conversion on the Mandarin voice

The Eloquence Engine's only Chinese data (`chs.syn`) is Simplified-script Mandarin; it cannot pronounce Traditional-exclusive characters (issue #98). We decided to apply phrase-aware Traditional-to-Simplified Script Conversion during Text Preprocessing — unconditionally, whenever the Mandarin Chinese Voice Identity is active — using vendored OpenCC data tables and a small in-repo longest-match converter.

## Considered Options

- **A real Traditional Chinese voice** (`eciTaiwaneseMandarin`, 0x60001): the ECI API defines it and IBM shipped it, but the engine data (`cht50.syn`) is lost — the NVDA community has searched and never found a copy (NVDA-IBMTTS-Driver #57). Not possible.
- **Cantonese voice data** (`ctt`, 0xB0001, Big5): circulates informally but has murky provenance, and Cantonese is a different spoken language — it would not serve Taiwanese users, who want Mandarin. Out of scope; documented as a known limitation for Hong Kong users.
- **Convert only when zh-TW/zh-Hant is requested**: rejected — document language tags are unreliable, and issue #98's text arrived untagged. Conversion is a near-no-op on Simplified text, so running it unconditionally on the Mandarin path is safe and simpler.
- **Vendor a conversion library** (opencc-python-reimplemented, zhconv): rejected — the linguistic knowledge is in OpenCC's ~40 KB of data tables; the matching algorithm is ~30 lines. We ship the tables verbatim (auditable against upstream, Apache-2.0) and own the matcher.

## Consequences

- Traditional Chinese is read with Mandarin pronunciation and mainland lexicon; correct for Taiwanese users, a better-than-nothing fallback for Hong Kong users (no Cantonese). Colloquial written-Cantonese characters remain unpronounceable.
- The voice list still advertises only `zh-CN`; zh-TW-localized NVDA does not auto-select the Chinese voice on first run.
- There is no user setting: Script Conversion is in the same category as the unconditional crash-prevention fixes.

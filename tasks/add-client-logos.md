# Plan: Add client logos (ConGlobal, Infrabridge, Silicon Labs, ESAB)

## Goal
Add four client logos to the live marketing site (`ragarwal.io-redesign/` → production
`thearistotle.ai`) as official brand logos in a new "Trusted by" strip directly below the
hero. Logos sourced as SVG (preferred) or hi-res PNG, verified to be the correct corporate
entity, stored locally, and rendered in a cohesive responsive strip.

Decisions locked (do not re-litigate):
- **Official brand logos (color SVG)** — not monochrome wordmarks. Why: user chose authenticity.
- **Placement: new "Trusted by" band immediately after hero**, before Comparison
  (`index.html:72`). Why: canonical social-proof position; site has no existing client section
  (the `cred-alumni` row is *alumni firms*, and `images/logos/*.svg` are orphaned/unused).
- Visual treatment: grayscale + color-on-hover for cohesion across mismatched marks
  (adjustable to always-color).

Scope fence: deliberately does NOT touch the orphaned `images/logos/{blackrock,mckinsey,nyu,
umich}.svg` or the alumni-firms text row. Does NOT commit/push (live deploy) — leave for human.

## Stage 0 — Deep-research the correct logos (THE key step)

For each company there is a name-collision / wrong-entity risk, so identity is verified BEFORE
downloading any asset. Run via `/deep-research` (or WebSearch + WebFetch fan-out), one verified
record per company:

| Name typed | Likely entity to confirm | Disambiguation signal |
|---|---|---|
| ConGlobal | ConGlobal (intermodal/container terminal & logistics, conglobal.com) | logistics/industrials |
| Infrabridge | InfraBridge (infrastructure asset manager, ex-AMP Capital, infrabridge.com) | infra PE/fund — NOT a SaaS "Infrabridge" |
| Silicon Labs | Silicon Laboratories Inc. (NASDAQ: SLAB, silabs.com) | semiconductors |
| ESAB | ESAB Corporation (NYSE: ESAB, esab.com) | welding/cutting industrial |

For each, the research must return:
1. **Official domain** (confirm against an independent source — Wikipedia/Crunchbase/ticker).
2. **Sector sanity check** — matches a sector Aristotle serves (industrials / semis & hardware).
   A mismatch ⇒ wrong entity, stop and flag.
3. **Logo source URL**, in priority order:
   - Official press/brand/media-kit page (best — licensed, true colors).
   - Wikimedia Commons SVG (well-curated, public).
   - Reputable SVG aggregator (e.g. brand asset sites) as last resort.
4. **File**: prefer `.svg`; else ≥2x PNG with transparent background.
5. **Trademark note**: capture any usage restriction the brand kit states.

Deliverable of Stage 0: a short table (entity, domain, source URL, file format, TM note) posted
back for a 30-second confirm before any file is written. This is the gate — get identity right
once, not after it ships to production.

## Stage 1 — Acquire & normalize assets
- Download each verified logo into `ragarwal.io-redesign/images/clients/` as
  `conglobal.svg`, `infrabridge.svg`, `siliconlabs.svg`, `esab.svg` (or `.png`).
- Normalize SVGs: strip width/height (keep `viewBox`) so CSS controls size; remove embedded
  XML cruft; ensure transparent bg. Sanity-open each to confirm it renders.
- Independently verify each downloaded mark visually = the right company (no placeholder/404 art).

## Stage 2 — Markup
Insert a new section after the hero `</section>` (`index.html:72`):
```html
<!-- ─── CLIENT LOGOS ─── -->
<section class="clients" aria-label="Selected clients">
  <p class="clients-eyebrow">Trusted by leading operators</p>
  <div class="clients-row">
    <img src="images/clients/conglobal.svg"  alt="ConGlobal" class="client-logo">
    <img src="images/clients/infrabridge.svg" alt="InfraBridge" class="client-logo">
    <img src="images/clients/siliconlabs.svg" alt="Silicon Labs" class="client-logo">
    <img src="images/clients/esab.svg"        alt="ESAB" class="client-logo">
  </div>
</section>
```

## Stage 3 — Styles (`styles.css`, near `.cred` block ~line 700)
- `.clients`: padding `clamp(48px,6vw,72px) 0`, centered, `background: var(--bg)`.
- `.clients-eyebrow`: muted, uppercase, letter-spaced, small — matches existing eyebrow style.
- `.clients-row`: flex, `wrap`, `justify-content:center`, `align-items:center`, `gap: clamp(32px,5vw,64px)`.
- `.client-logo`: `height: clamp(24px,3vw,34px)`, `width:auto`, `filter: grayscale(1)`,
  `opacity:.65`, transition; `:hover` → `filter:none; opacity:1`. Normalizes height across
  wide (ConGlobal) and tall marks; visual cohesion despite mixed brand colors.
- Mobile: confirm 4 logos wrap to 2×2 cleanly at <600px.

## Stage 4 — Verify
- Open `index.html` in browser (or existing test harness in `tests/`): logos load (no 404s),
  align on one baseline, wrap correctly mobile, hover works, alt text present.
- Run `tests/` if present; check no console errors.
- Confirm no unintended diff to other sections.

## Risks / open items
- **Trademark/permission**: showing client logos implies endorsement; some brand kits require
  written consent. Flag any restriction found in Stage 0 — business call, not mine to assume.
- **Entity ambiguity** (esp. "Infrabridge") — mitigated by Stage 0 sector sanity check.
- **Deploy**: push to main = production deploy of thearistotle.ai — leave commit/push to human.

## v2 (2026-09-09) — set change, bigger, one row
- Set is now ESAB, Silicon Labs, [Quant Global], InfraBridge, Schneider Electric. ConGlobal
  removed per the new "full logo set"; `images/clients/conglobal.png` left on disk in case
  that was unintended.
- Schneider Electric: `images/clients/schneider.svg`, official mark from Wikimedia Commons
  (`File:Schneider_Electric_2007.svg`), width/height stripped → `viewBox="0 0 188.74 57"`.
- Sizes ~1.5× (32–54px desktop, 30–36px phone). `.clients-row` is `nowrap` down to 600px so
  the logos always display across; wraps only on small phones.
- **Quant Global: BLOCKED on identity.** No client record locally; web candidates are
  unrelated (NY investment adviser, Malaysian FX-training shop, "QUANT AI Lab" in Paris).
  Markup slot + CSS height rule are in place (commented `<img>` in `index.html`); confirm the
  entity/domain, drop `images/clients/quantglobal.svg`, uncomment.

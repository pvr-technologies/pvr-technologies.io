#!/usr/bin/env python3
"""
Offline + online checks for the Aristotle site.

Offline checks run against the repo files. The optional --online flag also
fetches the live URLs to verify HTTP status and content type.

Run:
    python3 tests/site_check.py            # offline checks only
    python3 tests/site_check.py --online   # also hit live URLs

Exit code is non-zero if any test fails. Designed for CI.
"""
from __future__ import annotations

import argparse
import html.parser
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
TEAM = ROOT / "team.html"
INSIGHTS = ROOT / "insights.html"
STYLES = ROOT / "styles.css"
CNAME = ROOT / "CNAME"

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"

failures: list[str] = []
passes: list[str] = []

HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        passes.append(name)
        print(f"  {PASS} {name}")
    else:
        msg = f"{name}" + (f" — {detail}" if detail else "")
        failures.append(msg)
        print(f"  {FAIL} {msg}")


def skip(reason: str) -> None:
    print(f"  \033[33m!\033[0m {reason}")


def cname_configured() -> bool:
    """True when this checkout serves a GitHub Pages custom domain.

    CNAME is absent once the domain is removed in Settings -> Pages (GitHub
    commits "Delete CNAME" itself, e.g. when the domain moved to another repo),
    so every CNAME assertion is conditional on the file being present.
    """
    return CNAME.is_file()


def strip_comments(text: str) -> str:
    return HTML_COMMENT_RE.sub("", text)


# ---------- Parse helpers ----------


class IDCollector(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.imgs: list[tuple[str, str]] = []
        self.hrefs: list[str] = []
        self.tags_open: list[str] = []
        self.tag_counter: Counter[str] = Counter()
        self.section_h2s: list[str] = []
        self._in_h2 = False
        self._h2_buf: list[str] = []
        self.errors: list[str] = []
        self.has_viewport = False
        self.button_min_heights: list[str] = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        self.tag_counter[tag] += 1
        if "id" in attr and attr["id"]:
            self.ids.append(attr["id"])
        if tag == "img":
            self.imgs.append((attr.get("src") or "", attr.get("alt") or ""))
        if tag == "a" and "href" in attr and attr["href"]:
            self.hrefs.append(attr["href"])
        if tag == "h2":
            self._in_h2 = True
            self._h2_buf = []
        if tag == "meta" and attr.get("name") == "viewport":
            content = attr.get("content", "")
            self.has_viewport = "width=device-width" in content
        if tag in {"div", "section", "nav", "footer", "header", "main", "article"}:
            self.tags_open.append(tag)

    def handle_endtag(self, tag):
        if tag == "h2" and self._in_h2:
            self._in_h2 = False
            self.section_h2s.append("".join(self._h2_buf).strip())
        if tag in {"div", "section", "nav", "footer", "header", "main", "article"}:
            if self.tags_open and self.tags_open[-1] == tag:
                self.tags_open.pop()
            else:
                self.errors.append(f"unbalanced </{tag}>")

    def handle_data(self, data):
        if self._in_h2:
            self._h2_buf.append(data)


def parse(path: Path) -> IDCollector:
    p = IDCollector()
    p.feed(path.read_text(encoding="utf-8"))
    return p


# ---------- Tests ----------


def test_files_exist() -> None:
    print("\n[1] File existence")
    check("index.html exists", INDEX.is_file())
    check("team.html exists", TEAM.is_file())
    check("insights.html exists", INSIGHTS.is_file())
    check("styles.css exists", STYLES.is_file())
    if cname_configured():
        check("CNAME exists", True)
    else:
        skip("CNAME absent — no custom domain configured in this repo; CNAME checks skipped")
    check("images/ dir exists", (ROOT / "images").is_dir())
    for fname in ("favicon.png", "sam.jpeg", "bia.jpeg", "rachit.jpeg"):
        check(f"images/{fname} exists", (ROOT / "images" / fname).is_file())


def test_cname() -> None:
    print("\n[2] CNAME format")
    if not cname_configured():
        skip("no CNAME in this checkout — skipped")
        return
    text = CNAME.read_text(encoding="utf-8").strip()
    check("CNAME is not empty", bool(text))
    check("CNAME has exactly one domain", len(text.splitlines()) == 1)
    valid_domains = {"aristotletechnology.com", "thearistotle.ai"}
    check(
        f"CNAME is one of {sorted(valid_domains)}",
        text in valid_domains,
        f"got {text!r}",
    )
    check(
        "CNAME has no trailing whitespace",
        text == text.strip(),
    )
    check(
        "CNAME is valid domain syntax",
        bool(re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", text)),
        f"got {text!r}",
    )


def test_html_head(p: IDCollector, label: str, path: Path) -> None:
    print(f"\n[3] HTML head ({label})")
    text = path.read_text(encoding="utf-8")
    check("has DOCTYPE", text.lstrip().lower().startswith("<!doctype html>"))
    check("html lang attr present", 'lang="en"' in text)
    check("has <title>", "<title>" in text and "</title>" in text)
    check(
        "title mentions Aristotle",
        bool(re.search(r"<title>[^<]*Aristotle[^<]*</title>", text)),
    )
    check("has meta viewport (device-width)", p.has_viewport)
    check("has favicon link", 'rel="icon"' in text)
    check("references favicon.png", "images/favicon.png" in text)
    check("links external styles.css", 'href="styles.css"' in text)


def test_anchors(p: IDCollector, label: str) -> None:
    print(f"\n[4] Anchor integrity ({label})")
    ids = set(p.ids)
    check(
        "no duplicate IDs",
        len(p.ids) == len(ids),
        f"dupes: {[i for i,c in Counter(p.ids).items() if c>1]}",
    )
    anchors = [h for h in p.hrefs if h.startswith("#") and h != "#"]
    missing = [a for a in anchors if a[1:] not in ids]
    check(
        f"all {len(anchors)} internal anchors resolve",
        not missing,
        f"missing IDs: {sorted(set(missing))}",
    )
    check("no empty hrefs", "" not in p.hrefs)
    check(
        "no href='#' (lazy placeholder)",
        "#" not in p.hrefs,
        f"found bare '#' link in {label}",
    )
    check("no javascript: URLs", not any(h.lower().startswith("javascript:") for h in p.hrefs))


def test_images(p: IDCollector, where: str) -> None:
    print(f"\n[5] Images ({where})")
    for src, alt in p.imgs:
        if src.startswith(("http://", "https://", "data:")):
            continue
        path = ROOT / src
        check(f"image file exists: {src}", path.is_file())
        check(f"image has alt text: {src}", bool(alt.strip()), f"alt={alt!r}")


def test_required_sections(p: IDCollector) -> None:
    print("\n[6] Required sections present (index.html)")
    h2_text = " | ".join(p.section_h2s)
    # Path-merged section removed; platform now lives in its own section without an H2.
    expected_h2 = [
        ("comparison", "Ours Does"),
        ("credibility", "Decades of expertise"),
        ("sectors", "Leaders Across Various Sectors"),
        ("cases", "Case Studies"),
        ("qa", "Q&A"),
    ]
    for label, needle in expected_h2:
        check(
            f"section <{label}> headline contains {needle!r}",
            needle in h2_text,
            f"got: {h2_text}",
        )
    # Platform sub-band is now an h3 inside the merged path section; check the file directly
    raw = INDEX.read_text(encoding="utf-8")
    check(
        "platform sub-band h3 'Growth Meets'",
        bool(re.search(r"<h3[^>]*class=\"platform-h3\"[^>]*>.*?Growth Meets.*?</h3>", raw, re.DOTALL)),
    )

    expected_ids = [
        "hero", "value", "different", "offerings",
        "expertise", "sectors", "cases", "qa", "contact",
    ]
    actual_ids = set(p.ids)
    for sid in expected_ids:
        check(f"section id='{sid}' exists", sid in actual_ids)


def test_design_tokens() -> None:
    print("\n[7] Design tokens (styles.css)")
    text = STYLES.read_text(encoding="utf-8")
    expected = {
        "--bg": r"--bg:\s*#[0-9A-Fa-f]{3,8}",
        "--bg-alt": r"--bg-alt:\s*#[0-9A-Fa-f]{3,8}",
        "--navy": r"--navy:\s*#[0-9A-Fa-f]{3,8}",
        "--navy-deep": r"--navy-deep:\s*#[0-9A-Fa-f]{3,8}",
        "--ink": r"--ink:\s*#[0-9A-Fa-f]{3,8}",
        "--ink-muted": r"--ink-muted:\s*#[0-9A-Fa-f]{3,8}",
        "--line": r"--line:\s*#[0-9A-Fa-f]{3,8}",
        "--white": r"--white:\s*#[0-9A-Fa-f]{3,8}",
        "--radius-pill": r"--radius-pill:\s*\d+px",
    }
    for name, pattern in expected.items():
        check(f"token defined: {name}", bool(re.search(pattern, text)))


def test_content_invariants() -> None:
    print("\n[8] Content invariants (index.html)")
    text = INDEX.read_text(encoding="utf-8")
    visible = strip_comments(text)

    # Hero
    check(
        "hero headline category claim",
        "AI-native growth" in visible and "pricing advisory" in visible,
    )
    check("hero CTA: 'Book a Meeting'", "Book a Meeting" in visible)

    # Hero video background slot
    check(
        "hero has video bg slot (.hero-bg)",
        'class="hero-bg"' in text,
    )
    check(
        "hero video TODO comment references hero-chicago.mp4",
        "hero-chicago.mp4" in text,
    )
    check(
        "hero has dark overlay for text readability",
        'class="hero-overlay"' in text,
    )

    # Stat band — refreshed claims (40x ROI, <1 month, $2B+, Tied to impact)
    check("stat band: '40x'", "40x" in visible)
    check(
        "stat band: '<1 month' or '&lt;1 month'",
        "&lt;1 month" in visible or "<1 month" in visible,
    )
    check("stat band: '$2B+'", "$2B+" in visible)
    check("stat band: 'Tied to impact'", "Tied to impact" in visible)

    # Press strip removed (firms moved under credibility; only McKinsey + BLACKSTONE remain)
    check("press strip removed (no <section class=\"press\">)", 'class="press"' not in text)
    check("cred-alumni block present under credibility", 'class="cred-alumni"' in text)
    check("alumni: McKinsey under credibility", "McKinsey" in visible)
    check("alumni: BLACKSTONE under credibility", "BLACKSTONE" in visible)
    # Removed alumni firms must not appear as alumni labels (we only check the
    # cred-alumni block — these firms can still appear in body copy / Q&A naming MBB).
    cred_alumni_match = re.search(r'<div class="cred-alumni"[^>]*>(.*?)</div>', text, re.DOTALL)
    cred_alumni_text = cred_alumni_match.group(1) if cred_alumni_match else ""
    for firm in ("BCG", "Bain", "Deloitte", "KKR"):
        check(
            f"removed alumni label not present in cred-alumni: {firm!r}",
            firm not in cred_alumni_text,
        )

    # Path-merged section was removed; the funnel + partner cards no longer exist.
    # The pillars survived the cleanup and now sit under the platform section.

    # Pillars
    for pillar in ("Partner-Led", "AI Advantage", "Faster to Impact", "Capability Embedded"):
        check(f"pillar present: {pillar!r}", pillar in visible)

    # Product showcase
    check("product: 'Growth Meets'", "Growth Meets" in visible)
    check("product capability: 'Opportunity Identification'", "Opportunity Identification" in visible)
    check("product capability: 'Pricing'", "Pricing" in visible)
    check("product capability: 'Demand Forecasting'", "Demand Forecasting" in visible)
    check(
        "product capability: 'Geospatial Mapping' or 'Growth Analytics'",
        "Geospatial Mapping" in visible or "Growth Analytics" in visible,
    )

    # Credibility band
    check("cred: '$1B+'", "$1B+" in visible)
    check("cred: '50+'", "50+" in visible)

    # Sectors
    for sector in ("Industrials", "Semis", "Tech &amp; SaaS",
                   "Distribution", "Consumer", "PE"):
        check(f"sector present: {sector!r}", sector in visible)

    # Comparison columns
    for col in ("Aristotle", "No Advisor", "Big-3 (MBB)", "Boutiques"):
        check(f"comparison column: {col!r}", col in visible)

    # Closing CTA
    check(
        "closing CTA headline mentions '$100M' and '30 Days'",
        "$100M" in visible and "30 Days" in visible,
    )
    # Closing CTA uses a single 'Book a Meeting' button (simplified from earlier
    # dual-CTA). 'Book a Meeting' already asserted in hero check above.

    # Regressions: old design state must NOT appear
    check("regression: no 'BlackRock'", "BlackRock" not in visible and "Blackrock" not in visible)
    check("regression: no old 'Start a Conversation' CTA", "Start a Conversation" not in visible)
    check("regression: no flywheel remnants", "flywheel" not in text.lower())
    check("regression: no 'Playfair Display' headline font", "Playfair Display" not in text)
    check("regression: no old logo-strip section in index", "logo-strip" not in text)


def test_team_page(p: IDCollector) -> None:
    print("\n[8b] Team page (team.html)")
    text = TEAM.read_text(encoding="utf-8")
    visible = strip_comments(text)
    check("Sam Garg present", "Sam Garg" in visible)
    check("Beatriz Abramof present", "Beatriz Abramof" in visible)
    check("Rachit Agarwal present", "Rachit Agarwal" in visible)
    check("3 team cards", text.count('class="team-card') == 3 or text.count("team-bio-card") == 3)
    check("nav links back to index.html", 'href="index.html"' in text)
    check("nav has Insights link", 'href="insights.html"' in text)


def test_insights_page(p: IDCollector) -> None:
    print("\n[8c] Insights page (insights.html)")
    text = INSIGHTS.read_text(encoding="utf-8")
    visible = strip_comments(text)
    check("page title 'Notes from the field'", "Notes from the field" in visible)
    insight_cards = text.count('class="insight-card"')
    check(f"insight cards count >= 3 (got {insight_cards})", insight_cards >= 3)
    check("nav links back to index.html", 'href="index.html"' in text)
    check("nav has Team link", 'href="team.html"' in text)


def test_structural_counts() -> None:
    print("\n[9] Structural counts (index.html)")
    text = INDEX.read_text(encoding="utf-8")

    pillars = len(re.findall(r'<div class="pillar">', text))
    check(f"pillars section has 4 cards (got {pillars})", pillars == 4)

    stat_items = len(re.findall(r'<div class="stat">', text))
    check(f"stat band has 4 items (got {stat_items})", stat_items == 4)

    qa_items = len(re.findall(r'<details class="qa-item">', text))
    check(f"Q&A has 6 entries (got {qa_items})", qa_items == 6)

    case_cards = len(re.findall(r'class="case-card', text))
    check(f"case studies has 4 cards (got {case_cards})", case_cards == 4)

    sectors = len(re.findall(r'<div class="sector">', text))
    check(f"sectors grid has 6 tiles (got {sectors})", sectors == 6)

    cred_stats = len(re.findall(r'class="cred-num"', text))
    check(f"credibility band has 3 numerals (got {cred_stats})", cred_stats == 3)

    # Platform pillars are 4 tabbed <button>s, one per capability area
    pillar_tabs = len(re.findall(r'<button[^>]*class="pillar-tab(?:[^"]*)"', text))
    check(f"platform has 4 pillar tabs (got {pillar_tabs})", pillar_tabs == 4)
    check(
        "pillar tabs have aria-selected for a11y",
        text.count('aria-selected="true"') >= 1
        and text.count('aria-selected="false"') >= 3,
    )
    pillar_panels = len(re.findall(r'class="pillar-panel(?:\s[^"]*)?"', text))
    check(
        f"pillar panels present (one per tab, got {pillar_panels})",
        pillar_panels == 4,
    )
    check(
        "pillar deck handler wired in JS",
        ".pillar-tab" in text and "activatePillar" in text,
    )

    compare_rows = len(re.findall(r"<tr>\s*<td class=\"compare-row-label\">", text))
    check(f"comparison has 6 data rows (got {compare_rows})", compare_rows == 6)

    # Credibility alumni: exactly 2 labels (McKinsey + BLACKSTONE)
    cred_alumni_match = re.search(r'<div class="cred-alumni"[^>]*>(.*?)</div>', text, re.DOTALL)
    cred_alumni_inner = cred_alumni_match.group(1) if cred_alumni_match else ""
    alumni_labels = len(re.findall(r"<span>", cred_alumni_inner))
    check(
        f"credibility alumni has 2 labels (got {alumni_labels})",
        alumni_labels == 2,
    )


def test_no_visible_placeholders() -> None:
    print("\n[10] No visible placeholder text")
    case_insensitive = ["lorem ipsum"]
    case_sensitive = ["FIXME", "XXX:", "PLACEHOLDER", "TBD"]
    for path in (INDEX, TEAM, INSIGHTS):
        visible = strip_comments(path.read_text(encoding="utf-8"))
        for needle in case_insensitive:
            check(
                f"{path.name}: no visible '{needle}'",
                needle.lower() not in visible.lower(),
            )
        for needle in case_sensitive:
            check(
                f"{path.name}: no visible '{needle}'",
                needle not in visible,
            )


def test_balanced_tags(p: IDCollector, where: str) -> None:
    print(f"\n[11] Tag balance ({where})")
    check("no unbalanced section/div/nav/footer", not p.tags_open, f"left open: {p.tags_open}")
    check("HTML parser had no errors", not p.errors, f"errors: {p.errors[:3]}")
    text = (INDEX if where == "index.html" else TEAM if where == "team.html" else INSIGHTS).read_text(encoding="utf-8")
    check("<style> tags balanced", text.count("<style>") == text.count("</style>"))
    check("<script> tags balanced", text.count("<script>") == text.count("</script>"))


def test_mobile_friendly() -> None:
    """Static checks that the site is set up to render correctly on mobile.

    Cannot fully verify visual behavior without a headless browser, but these
    catch the common regressions: missing viewport tag, no media queries,
    wide tables not wrapped in horizontal-scroll containers, buttons too small
    to tap, missing hamburger menu for narrow viewports.
    """
    print("\n[12] Mobile-friendliness")

    css = STYLES.read_text(encoding="utf-8")

    # 1. Meta viewport — verified per-page via IDCollector elsewhere; re-affirm
    for path in (INDEX, TEAM, INSIGHTS):
        text = path.read_text(encoding="utf-8")
        check(
            f"{path.name}: meta viewport with device-width",
            'name="viewport"' in text and "width=device-width" in text,
        )

    # 2. CSS contains responsive breakpoints
    media_queries = re.findall(r"@media\s*\([^)]*max-width:\s*(\d+)px", css)
    breakpoints = sorted({int(m) for m in media_queries})
    check(
        f"styles.css declares max-width breakpoints (got {breakpoints})",
        len(breakpoints) >= 2 and min(breakpoints) <= 640,
        "expect at least 2 breakpoints, smallest <=640px",
    )

    # 3. prefers-reduced-motion guard
    check(
        "styles.css respects prefers-reduced-motion",
        "@media (prefers-reduced-motion: reduce)" in css,
    )

    # 4. Touch targets — .btn min-height >= 44px
    btn_match = re.search(r"\.btn\s*\{[^}]*min-height:\s*(\d+)px", css)
    if btn_match:
        check(
            f".btn min-height >= 44px (got {btn_match.group(1)}px)",
            int(btn_match.group(1)) >= 44,
        )
    else:
        check(".btn defines min-height for touch target", False, "no min-height on .btn")

    # 5. Wide tables are wrapped in horizontal-scroll containers
    compare_block = re.search(r"\.compare-wrap\s*\{[^}]*\}", css, re.DOTALL)
    check(
        ".compare-wrap has overflow-x scroll",
        bool(compare_block) and "overflow-x:" in compare_block.group(0),
    )
    product_block = re.search(r"\.product-mock\s*\{[^}]*\}", css, re.DOTALL)
    check(
        ".product-mock has overflow-x scroll",
        bool(product_block) and "overflow-x:" in product_block.group(0),
    )

    # 6. Mobile nav: hamburger button must exist in HTML + CSS
    for path in (INDEX, TEAM, INSIGHTS):
        text = path.read_text(encoding="utf-8")
        check(
            f"{path.name}: hamburger button present",
            "nav-hamburger" in text and 'aria-controls="nav-links"' in text,
        )
        check(
            f"{path.name}: hamburger has aria-expanded",
            'aria-expanded="false"' in text,
        )
    check(
        ".nav-hamburger styled and toggleable in styles.css",
        ".nav-hamburger" in css and ".nav-links.open" in css,
    )

    # 7. Section-level grids must collapse on mobile (must have grid-template-columns: 1fr
    #    somewhere inside a max-width media query for stacked rendering)
    mobile_blocks = re.findall(r"@media\s*\([^)]*max-width:[^)]*\)\s*\{[\s\S]*?(?:\}\s*\}|\}\s*$)", css)
    has_single_col_collapse = any(
        "grid-template-columns: 1fr" in block for block in mobile_blocks
    )
    check(
        "CSS collapses grids to single column on mobile",
        has_single_col_collapse,
        "no `grid-template-columns: 1fr` inside any max-width media query",
    )

    # 8. No inline `width:` greater than 720px on any element
    inline_widths = re.findall(r'style="[^"]*width:\s*(\d+)px', INDEX.read_text(encoding="utf-8"))
    too_wide = [int(w) for w in inline_widths if int(w) > 720]
    check(
        "no overly-wide inline widths in index.html",
        not too_wide,
        f"got {too_wide}",
    )

    # 9. Body font-size readable on mobile (>=15px declared)
    body_size = re.search(r"body\s*\{[^}]*font-size:\s*(\d+)px", css)
    check(
        f"body font-size >= 15px (got {body_size.group(1) if body_size else 'none'}px)",
        bool(body_size) and int(body_size.group(1)) >= 15,
    )

    # 10. Hero h1 has overflow-wrap or word-break so long phrases don't overflow on narrow screens
    hero_h1 = re.search(r"\.hero h1\s*\{[^}]*\}", css, re.DOTALL)
    check(
        ".hero h1 wraps long words on narrow viewports",
        bool(hero_h1) and ("overflow-wrap" in hero_h1.group(0) or "word-break" in hero_h1.group(0)),
    )


def test_online() -> None:
    print("\n[13] Online checks")
    branch_url = (
        "https://raw.githubusercontent.com/ragarwal23/ragarwal.io/"
        "redesign-eilla/index.html"
    )
    try:
        with urllib.request.urlopen(branch_url, timeout=15) as r:
            r.read(1024)
        check("redesign-eilla branch index.html fetched", True)
    except Exception as e:
        check("redesign-eilla branch index.html fetched", False, str(e))

    if cname_configured():
        main_cname_url = (
            "https://raw.githubusercontent.com/ragarwal23/ragarwal.io/main/CNAME"
        )
        try:
            with urllib.request.urlopen(main_cname_url, timeout=15) as r:
                cname_main = r.read().decode().strip()
            check(
                "main CNAME is a known domain",
                cname_main in {"aristotletechnology.com", "thearistotle.ai"},
                f"got {cname_main!r}",
            )
        except Exception as e:
            check("main CNAME fetch", False, str(e))
    else:
        skip("no CNAME in this checkout — remote main CNAME check skipped")

    live = "https://thearistotle.ai/"
    try:
        with urllib.request.urlopen(live, timeout=10) as r:
            status = r.status
            body = r.read(4096).decode("utf-8", errors="replace")
        check(f"live site {live} returned 200", status == 200, f"status={status}")
        check(
            "live site is the Aristotle page",
            "Aristotle" in body or "ARISTOTLE" in body,
            "headline marker missing",
        )
    except Exception as e:
        print(f"  \033[33m!\033[0m live site {live} not yet reachable: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true")
    args = ap.parse_args()

    print(f"Aristotle site checks — repo: {ROOT}")

    index_p = parse(INDEX)
    team_p = parse(TEAM)
    insights_p = parse(INSIGHTS)

    test_files_exist()
    test_cname()
    test_html_head(index_p, "index.html", INDEX)
    test_html_head(team_p, "team.html", TEAM)
    test_html_head(insights_p, "insights.html", INSIGHTS)
    test_anchors(index_p, "index.html")
    test_images(index_p, "index.html")
    test_images(team_p, "team.html")
    test_images(insights_p, "insights.html")
    test_required_sections(index_p)
    test_design_tokens()
    test_content_invariants()
    test_team_page(team_p)
    test_insights_page(insights_p)
    test_structural_counts()
    test_no_visible_placeholders()
    test_balanced_tags(index_p, "index.html")
    test_balanced_tags(team_p, "team.html")
    test_balanced_tags(insights_p, "insights.html")
    test_mobile_friendly()
    if args.online:
        test_online()

    print()
    print(f"  {len(passes)} passed, {len(failures)} failed")
    if failures:
        print("\nFailures:")
        for f in failures:
            print(f"  {FAIL} {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

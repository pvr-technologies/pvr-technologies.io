#!/usr/bin/env python3
"""
Offline + online checks for the Aristotle site.

The site is plain HTML: one file per page at the repo root, nav and footer
copied into every page, one shared styles.css with a version tag, and a small
script at the bottom of each page. These checks keep that model honest.

Run:
    python3 tests/site_check.py            # offline checks only
    python3 tests/site_check.py --online   # also hit live URLs

Exit code is non-zero if any test fails. Designed for CI.
"""
from __future__ import annotations

import argparse
import html.parser
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
STYLES = ROOT / "styles.css"
CNAME = ROOT / "CNAME"
SITE = "https://thearistotle.ai/"
EMAIL = "sam@thearistotle.ai"

MAIN_PAGES = [
    "index.html", "platform.html", "technology.html", "security.html", "customers.html",
    "private-equity.html", "about.html", "demo.html", "contact.html",
]
APP_PAGES = [
    "pricing-intelligence.html", "customer-intelligence.html", "ready-reckoner.html", "market-sizing.html",
    "atlas.html", "forecasting.html", "research.html", "ask-aristotle.html",
]
STORY_PAGES = [
    "story-components-pricing.html", "story-industrial-growth.html",
    "story-private-equity-diligence.html", "story-semiconductor-forecasting.html",
]
PAGES = MAIN_PAGES + APP_PAGES + STORY_PAGES
# Old addresses that stay live as redirects so existing links keep working.
REDIRECTS = {"team.html": "about.html", "insights.html": "index.html"}
# Pages held back until their content is ready; nothing may link to them.
HELD = ["careers", "insights"]
# Section highlighted in the nav for each page (None: no highlight).
NAV_SECTION = {
    "index.html": None, "demo.html": None,
    "platform.html": "platform", "technology.html": "technology", "security.html": "technology",
    "customers.html": "customers", "private-equity.html": "pe", "about.html": "company", "contact.html": "company",
    **{p: "platform" for p in APP_PAGES}, **{p: "customers" for p in STORY_PAGES},
}
REMOVED_ASSETS = ["images/hero-chicago.mp4", "images/decks", "images/logos", "images/rachit.jpeg"]

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"

failures: list[str] = []
passes: list[str] = []


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


def github_repo() -> str:
    """owner/name of the GitHub repo this checkout belongs to."""
    env = os.environ.get("GITHUB_REPOSITORY")
    if env:
        return env
    try:
        url = subprocess.run(
            ["git", "-C", str(ROOT), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
        if m:
            return m.group(1)
    except (OSError, subprocess.CalledProcessError):
        pass
    return "pvr-technologies/pvr-technologies.io"


# ---------- Parsing ----------

BLOCK_TAGS = {"div", "section", "nav", "footer", "header", "main", "article", "aside", "form", "ul", "table"}


class Page(html.parser.HTMLParser):
    def __init__(self, path: Path) -> None:
        super().__init__(convert_charrefs=True)
        self.path = path
        self.name = path.name
        self.text = path.read_text(encoding="utf-8")
        self.ids: set[str] = set()
        self.links: list[dict] = []
        self.imgs: list[dict] = []
        self.meta: dict[str, str] = {}
        self.link_rel: dict[str, str] = {}
        self.titles: list[str] = []
        self.stack: list[str] = []
        self.errors: list[str] = []
        self._in_title = False
        self.visible: list[str] = []
        self._skip = 0
        self.feed(self.text)
        self.close()
        self.visible_text = re.sub(r"\s+", " ", " ".join(self.visible))

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "a":
            self.links.append(a)
        elif tag == "img":
            self.imgs.append(a)
        elif tag == "meta":
            key = a.get("name") or a.get("property") or a.get("http-equiv")
            if key:
                self.meta[key] = a.get("content", "")
        elif tag == "link" and a.get("rel"):
            self.link_rel.setdefault(a["rel"], a.get("href", ""))
        elif tag == "title":
            self._in_title = True
            self.titles.append("")
        if tag in ("script", "style"):
            self._skip += 1
        if tag in BLOCK_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in ("script", "style"):
            self._skip -= 1
        if tag in BLOCK_TAGS:
            if self.stack and self.stack[-1] == tag:
                self.stack.pop()
            else:
                self.errors.append(f"unbalanced </{tag}>")

    def handle_data(self, data):
        if self._in_title:
            self.titles[-1] += data
        elif not self._skip:
            self.visible.append(data)

    def block(self, tag: str) -> str:
        m = re.search(rf"<{tag}\b.*?</{tag}>", self.text, re.S)
        return m.group(0) if m else ""

    def script(self) -> str:
        m = re.search(r"<script>(.*?)</script>", self.text, re.S)
        return m.group(1) if m else ""


def load(name: str) -> Page | None:
    path = ROOT / name
    return Page(path) if path.is_file() else None


def canonical_url(name: str) -> str:
    return SITE if name == "index.html" else SITE + name


# ---------- Tests ----------


def test_files_exist(pages: dict[str, Page | None]) -> None:
    print("\n[1] Files")
    for name in PAGES:
        check(f"{name} exists", pages[name] is not None)
    for name in REDIRECTS:
        check(f"{name} exists as a redirect", (ROOT / name).is_file())
    check("styles.css exists", STYLES.is_file())
    for rel in ["images/favicon.png", "images/apple-touch-icon.png", "images/social-preview.png"]:
        check(f"{rel} exists", (ROOT / rel).is_file())
    if cname_configured():
        check("CNAME exists", True)
    else:
        skip("no CNAME in this checkout — custom domain checks skipped")
    stray = sorted(p.name for p in ROOT.glob("*.html") if p.name not in PAGES and p.name not in REDIRECTS)
    check("no unexpected pages at the root", not stray, f"found {stray}")


def test_cname() -> None:
    print("\n[2] CNAME")
    if not cname_configured():
        skip("CNAME checks skipped")
        return
    text = CNAME.read_text(encoding="utf-8").strip()
    check("CNAME is not empty", bool(text))
    check("CNAME has exactly one domain", len(text.splitlines()) == 1)
    check("CNAME is thearistotle.ai", text == "thearistotle.ai", f"got {text!r}")


def test_heads(pages: dict[str, Page]) -> None:
    print("\n[3] Page heads")
    for name, p in pages.items():
        t = p.text
        check(f"{name}: DOCTYPE", t.lstrip().lower().startswith("<!doctype html>"))
        check(f"{name}: lang=\"en\"", '<html lang="en">' in t)
        check(f"{name}: one non-empty <title>", len(p.titles) == 1 and bool(p.titles[0].strip()))
        if name != "index.html":
            check(f"{name}: title ends with '| Aristotle'", p.titles[0].strip().endswith("| Aristotle"), p.titles[0])
        desc = p.meta.get("description", "")
        check(f"{name}: description present and under 200 characters", 0 < len(desc) <= 200, f"{len(desc)} chars")
        check(f"{name}: viewport set to device width", "width=device-width" in p.meta.get("viewport", ""))
        check(f"{name}: favicon is images/favicon.png", p.link_rel.get("icon") == "images/favicon.png")
        url = canonical_url(name)
        check(f"{name}: canonical URL", p.link_rel.get("canonical") == url, p.link_rel.get("canonical", ""))
        check(f"{name}: og:url matches canonical", p.meta.get("og:url") == url)
        og_img = p.meta.get("og:image", "")
        check(f"{name}: og:image is an absolute https URL", og_img.startswith(SITE))
        check(f"{name}: og:image file exists", (ROOT / og_img.replace(SITE, "")).is_file())
        check(f"{name}: og:title and twitter:title set", bool(p.meta.get("og:title")) and bool(p.meta.get("twitter:title")))
        check(f"{name}: links styles.css with a version", bool(re.search(r'href="styles\.css\?v=[^"]+"', t)))


def test_stylesheet_version(pages: dict[str, Page]) -> None:
    print("\n[4] Stylesheet version")
    versions = {m.group(1) for p in pages.values() for m in re.finditer(r'href="styles\.css\?v=([^"]+)"', p.text)}
    check("all pages pin the same stylesheet version", len(versions) == 1, f"got {sorted(versions)}")


def test_links(pages: dict[str, Page]) -> None:
    print("\n[5] Links")
    for name, p in pages.items():
        bad: list[str] = []
        for a in p.links:
            if "href" not in a:
                continue  # illustrative app screens use <a> without href as tab labels
            href = a["href"]
            if not href or href == "#" or href.lower().startswith("javascript:"):
                bad.append(f"empty or script link {href!r}")
                continue
            parsed = urlparse(href)
            if parsed.scheme in ("http", "https"):
                if a.get("target") == "_blank" and "noopener" not in a.get("rel", ""):
                    bad.append(f"{href} opens a new tab without rel=noopener")
                continue
            if parsed.scheme == "mailto":
                if parsed.path != EMAIL:
                    bad.append(f"mail link to {parsed.path}, expected {EMAIL}")
                continue
            target_name = parsed.path or name
            target = pages.get(target_name)
            if target is None:
                bad.append(f"{href} points to a missing page")
                continue
            if parsed.fragment and parsed.fragment not in target.ids:
                bad.append(f"{href} points to a missing anchor")
        check(f"{name}: every link resolves", not bad, "; ".join(bad[:5]))
        held = [a["href"] for a in p.links if "href" in a and any(h in a["href"] for h in HELD)]
        check(f"{name}: no links to held pages (careers, insights)", not held, str(held))


def test_images(pages: dict[str, Page]) -> None:
    print("\n[6] Images")
    for name, p in pages.items():
        missing = [i.get("src", "") for i in p.imgs if not (ROOT / i.get("src", "")).is_file()]
        check(f"{name}: every image file exists", not missing, str(missing))
        check(f"{name}: every image has alt text", all(i.get("alt", "").strip() for i in p.imgs))
        check(f"{name}: no embedded data images", "base64," not in p.text)


def test_shared_chrome(pages: dict[str, Page]) -> None:
    print("\n[7] Nav and footer copied identically into every page")
    def plain_nav(p: Page) -> str:
        return p.block("nav").replace(' class="on"', "")
    ref = pages["index.html"]
    for name, p in pages.items():
        check(f"{name}: nav matches index.html", plain_nav(p) == plain_nav(ref))
        check(f"{name}: footer matches index.html", p.block("footer") == ref.block("footer"))
        marked = re.findall(r'data-sec="([^"]+)" class="on"', p.block("nav"))
        want = NAV_SECTION[name]
        check(f"{name}: nav highlights {want or 'nothing'}", marked == ([want] if want else []), str(marked))


def test_content(pages: dict[str, Page]) -> None:
    print("\n[8] Content")
    for name, p in pages.items():
        check(f"{name}: no placeholder highlights", 'class="ph"' not in p.text)
        low = p.visible_text.lower()
        for phrase in ["to confirm", "mockup", "lorem", "todo", "rachit"]:
            check(f"{name}: no '{phrase}' in visible text", phrase not in low)
    about = pages["about.html"].visible_text
    check("about: Sam Garg on the team", "Sam Garg" in about)
    check("about: Beatriz Abramof on the team", "Beatriz Abramof" in about)
    check("about: Sam's McKinsey title", "Associate Partner" in about)
    check("about: timeline starts 2016 to 2024", "2016 to 2024" in about)
    check("about: no advisors or investors cards", "Advisors" not in about and "Investors" not in about)
    check("contact: single address shown", EMAIL in pages["contact.html"].visible_text)
    demo = pages["demo.html"]
    check("demo: form opens an email to sam@", f"mailto:{EMAIL}" in demo.script())
    for field in ["first_name", "last_name", "email", "company", "role", "revenue", "interest", "message"]:
        check(f"demo: form field '{field}' is named", f'name="{field}"' in demo.text)
    story = pages["story-components-pricing.html"].visible_text
    check("pricing story: client quote present", "strategic platform" in story and "VP, Pricing" in story)
    others = [n for n in STORY_PAGES if n != "story-components-pricing.html"]
    check("other stories: no empty client quote block", all("Client view" not in pages[n].visible_text for n in others))


def test_structure(pages: dict[str, Page]) -> None:
    print("\n[9] Structure")
    for name, p in pages.items():
        check(f"{name}: block tags balanced", not p.errors and not p.stack, f"{p.errors[:3]} open: {p.stack[:3]}")
        check(f"{name}: one nav, one main, one footer",
              p.text.count("<nav") == 1 and p.text.count("<main>") == 1 and p.text.count("<footer") == 1)
        check(f"{name}: one script at the bottom", p.text.count("<script") == 1 and
              p.text.rstrip().endswith("</script>\n</body>\n</html>".strip()))


def test_page_scripts(pages: dict[str, Page]) -> None:
    print("\n[10] Each page carries only the script it needs")
    for name, p in pages.items():
        s = p.script()
        check(f"{name}: mobile menu wired", ".burger" in s and 'class="burger"' in p.text)
        for marker, fn in [('class="carousel"', "initCarousel"), ('class="console"', "playRun"), ('class="fc', "querySelectorAll('.fc')")]:
            has_markup, has_code = marker in p.text, fn in s
            check(f"{name}: {fn} present only where needed", has_markup == has_code,
                  f"markup={has_markup} code={has_code}")


def test_redirects() -> None:
    print("\n[11] Old addresses redirect")
    for old, new in REDIRECTS.items():
        path = ROOT / old
        if not path.is_file():
            check(f"{old} redirect exists", False)
            continue
        t = path.read_text(encoding="utf-8")
        check(f"{old} refreshes to {new}", f'content="0; url={new}"' in t)
        check(f"{old} links to {new}", f'href="{new}"' in t)
        check(f"{old} is not indexed", 'name="robots" content="noindex"' in t)


def test_assets(pages: dict[str, Page]) -> None:
    print("\n[12] Assets")
    for rel in REMOVED_ASSETS:
        check(f"{rel} removed", not (ROOT / rel).exists())
    used = " ".join(p.text for p in pages.values()) + STYLES.read_text(encoding="utf-8")
    orphans = sorted(str(f.relative_to(ROOT)) for f in (ROOT / "images").rglob("*")
                     if f.is_file() and f.name != ".DS_Store" and str(f.relative_to(ROOT)) not in used)
    check("every file in images/ is used", not orphans, str(orphans))


def test_mobile() -> None:
    print("\n[13] Mobile")
    css = STYLES.read_text(encoding="utf-8")
    widths = sorted({int(w) for w in re.findall(r"@media \(max-width:\s*(\d+)px\)", css)})
    check("styles.css has phone and tablet breakpoints", any(w <= 640 for w in widths) and any(w >= 900 for w in widths), str(widths))
    check("burger menu shown on small screens", ".burger{display:block}" in css)
    check("no mockup helper rules left", ".mk" not in css and ".ph{" not in css)


def test_online() -> None:
    print("\n[14] Online checks")
    if cname_configured():
        main_cname_url = f"https://raw.githubusercontent.com/{github_repo()}/main/CNAME"
        try:
            with urllib.request.urlopen(main_cname_url, timeout=15) as r:
                cname_main = r.read().decode().strip()
            check("main CNAME is a known domain", cname_main in {"aristotletechnology.com", "thearistotle.ai"},
                  f"got {cname_main!r}")
        except Exception as e:
            check("main CNAME fetch", False, str(e))
    else:
        skip("no CNAME in this checkout — remote main CNAME check skipped")

    live = SITE
    try:
        with urllib.request.urlopen(live, timeout=10) as r:
            status = r.status
            body = r.read(4096).decode("utf-8", errors="replace")
        check(f"live site {live} returned 200", status == 200, f"status={status}")
        check("live site is the Aristotle page", "Aristotle" in body, "headline marker missing")
    except Exception as e:
        print(f"  \033[33m!\033[0m live site {live} not yet reachable: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true")
    args = ap.parse_args()

    print(f"Aristotle site checks — repo: {ROOT}")
    loaded = {name: load(name) for name in PAGES}
    test_files_exist(loaded)
    pages = {n: p for n, p in loaded.items() if p is not None}
    if len(pages) != len(PAGES):
        print("\nPages are missing; later checks need every page.")
    else:
        test_cname()
        test_heads(pages)
        test_stylesheet_version(pages)
        test_links(pages)
        test_images(pages)
        test_shared_chrome(pages)
        test_content(pages)
        test_structure(pages)
        test_page_scripts(pages)
        test_redirects()
        test_assets(pages)
        test_mobile()
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

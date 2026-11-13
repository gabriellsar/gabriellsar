#!/usr/bin/env python3
"""Gera os SVGs do README no sistema visual gabriel.s.

    python scripts/build.py            # estáticos + atividade (usa GITHUB_TOKEN se houver)
    python scripts/build.py --demo     # atividade com dados sintéticos, só para pré-visualizar

Tudo sai em assets/, em duas variantes: *-dark.svg e *-light.svg.
Fontes Geist são recortadas para os caracteres usados e embutidas em woff2.
"""
import base64
import datetime as dt
import io
import json
import math
import os
import random
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = ROOT / "fonts"
W = 840

# ---------------------------------------------------------------- tokens
THEMES = {
    "dark": {
        "bg": "#0a0c0d", "surface": "#10181a", "inset": "#0b1214", "line": "#1a2628",
        "line-strong": "#263638", "control": "#56696c", "dot": "#1c2729", "text": "#d3e1e2",
        "text-strong": "#f4fbfb", "muted": "#8fa3a6", "accent": "#7cf0d4", "on-accent": "#0a0c0d",
        "accent-subtle": "#12302b", "signal": "#ff8a6b", "signal-subtle": "#3a1d16",
        "caution": "#ffb454", "caution-subtle": "#33240f",
        "data": ["#7cf0d4", "#a8c5ff", "#ffb454", "#c9a8ff", "#8fa3a6"],
    },
    "light": {
        "bg": "#f3f6f6", "surface": "#fbfdfd", "inset": "#e8eeee", "line": "#dbe3e4",
        "line-strong": "#c4d0d1", "control": "#74878a", "dot": "#cfd9da", "text": "#1b2628",
        "text-strong": "#0a1012", "muted": "#53676b", "accent": "#0a735f", "on-accent": "#fbfdfd",
        "accent-subtle": "#d7f2ea", "signal": "#ad3a18", "signal-subtle": "#fbe3db",
        "caution": "#8a5200", "caution-subtle": "#f8ead2",
        "data": ["#0a735f", "#3d63c9", "#8a5200", "#7a4fd0", "#53676b"],
    },
}

FONT_FILES = {
    "sans200": "Geist-UltraLight.ttf",
    "sans300": "Geist-Light.ttf",
    "sans400": "Geist-Regular.ttf",
    "mono400": "GeistMono-Regular.ttf",
    "mono500": "GeistMono-Medium.ttf",
}


class Font:
    def __init__(self, key, path):
        self.key, self.path = key, path
        f = TTFont(path)
        self.upm = f["head"].unitsPerEm
        self.cmap = f.getBestCmap()
        self.hmtx = f["hmtx"].metrics
        self.asc = f["hhea"].ascent / self.upm
        self.desc = -f["hhea"].descent / self.upm
        self.cap = getattr(f["OS/2"], "sCapHeight", 700) / self.upm

    def adv(self, ch, size):
        g = self.cmap.get(ord(ch)) or self.cmap.get(ord("?"))
        return self.hmtx[g][0] / self.upm * size

    def width(self, s, size, ls=0.0):
        if not s:
            return 0.0
        return sum(self.adv(c, size) for c in s) + ls * size * (len(s) - 1)


FONT = {k: Font(k, FONTS / v) for k, v in FONT_FILES.items()}


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def f2(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------- documento
class Doc:
    def __init__(self, w, h, theme, title, bg=None):
        self.w, self.h, self.t, self.title = w, h, THEMES[theme], title
        self.els, self.used = [], defaultdict(set)
        self.bg = bg

    def c(self, name):
        return self.t[name]

    def add(self, s):
        self.els.append(s)

    def rect(self, x, y, w, h, fill=None, stroke=None, sw=1, extra=""):
        a = f'<rect x="{f2(x)}" y="{f2(y)}" width="{f2(w)}" height="{f2(h)}"'
        a += f' fill="{fill}"' if fill else ' fill="none"'
        if stroke:
            a += f' stroke="{stroke}" stroke-width="{sw}"'
        self.add(a + f"{extra}/>")

    def hline(self, x1, x2, y, color, sw=1):
        self.rect(x1, y, x2 - x1, sw, fill=color)

    def corner(self, x, y, color=None, size=12, sw=2):
        col = color or self.c("accent")
        self.rect(x, y, size, sw, fill=col)
        self.rect(x, y, sw, size, fill=col)

    def text(self, x, y, s, font, size, color, ls=0.0, anchor="start", extra=""):
        """y é a linha de base. ls em em. Texto com tracking leva posição por glifo."""
        F = FONT[font]
        self.used[font].update(s)
        w = F.width(s, size, ls)
        if anchor == "end":
            x -= w
        elif anchor == "middle":
            x -= w / 2
        attrs = f'xml:space="preserve" font-family="{font}" font-size="{f2(size)}" fill="{color}"{extra}'
        if ls and len(s) > 1:
            xs, cx = [], x
            for ch in s:
                xs.append(f2(cx))
                cx += F.adv(ch, size) + ls * size
            self.add(f'<text x="{" ".join(xs)}" y="{f2(y)}" {attrs}>{esc(s)}</text>')
        else:
            self.add(f'<text x="{f2(x)}" y="{f2(y)}" {attrs}>{esc(s)}</text>')
        return w

    def render(self, css_extra=""):
        faces = []
        for key, chars in sorted(self.used.items()):
            chars = "".join(sorted(chars | {" "}))
            faces.append(f"@font-face{{font-family:'{key}';src:url(data:font/woff2;base64,{woff2_subset(key, chars)}) format('woff2');}}")
        css = "".join(faces) + BASE_CSS + css_extra
        out = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{f2(self.h)}" viewBox="0 0 {self.w} {f2(self.h)}" role="img" aria-label="{esc(self.title)}">',
            f"<title>{esc(self.title)}</title>",
            f"<style>{css}</style>",
        ]
        if self.bg:
            out.append(f'<rect width="100%" height="100%" fill="{self.c(self.bg)}"/>')
        out += self.els
        out.append("</svg>")
        return "\n".join(out)


BASE_CSS = (
    "text{font-variant-numeric:tabular-nums;font-kerning:normal;}"
    ".blink{animation:blink 1.1s steps(1) infinite;}"
    "@keyframes blink{50%{opacity:0;}}"
    "@media (prefers-reduced-motion:reduce){.blink{animation:none;}}"
)

_subset_cache = {}


def woff2_subset(key, chars):
    k = (key, chars)
    if k not in _subset_cache:
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.layout_features = ["kern", "liga", "tnum"]
        opts.hinting = False
        opts.desubroutinize = True
        opts.name_IDs = []
        opts.drop_tables += ["meta"]
        font = subset.load_font(str(FONTS / FONT_FILES[key]), opts)
        sub = subset.Subsetter(opts)
        sub.populate(text=chars)
        sub.subset(font)
        buf = io.BytesIO()
        subset.save_font(font, buf, opts)
        _subset_cache[k] = base64.b64encode(buf.getvalue()).decode()
    return _subset_cache[k]


def baseline(top, size, lh, font):
    F = FONT[font]
    return top + (lh - (F.asc + F.desc) * size) / 2 + F.asc * size


def wrap(s, font, size, maxw, ls=0.0):
    words, lines, cur = s.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if FONT[font].width(t, size, ls) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- peças
def label(d, x, top, s, color=None, size=12, ls=0.14, anchor="start", font="mono400"):
    return d.text(x, baseline(top, size, 16, font), s, font, size, color or d.c("muted"), ls, anchor)


def tag(d, x, top, s, tone="neutral", dot=True):
    """Tag: 26px, padding 10, borda line-strong, mono 11 / 0.14em."""
    F = FONT["mono400"]
    tw = F.width(s, 11, 0.14)
    w = 20 + tw + (7 + 8 if dot else 0)
    border, fill, color, dotc = d.c("line-strong"), None, d.c("muted"), d.c("muted")
    if tone == "accent":
        color, dotc = d.c("text"), d.c("accent")
    elif tone in ("caution", "signal"):
        border, fill, color, dotc = d.c(tone), d.c(tone + "-subtle"), d.c(tone), d.c(tone)
    d.rect(x + 0.5, top + 0.5, w - 1, 25, fill=fill, stroke=border)
    tx = x + 10
    if dot:
        d.add(f'<circle cx="{f2(tx + 3.5)}" cy="{f2(top + 13)}" r="3.5" fill="{dotc}"/>')
        tx += 15
    d.text(tx, baseline(top, 11, 26, "mono400"), s, "mono400", 11, color, 0.14)
    return w


def terrain_dots(d, x0, y0, heights, rows, px, py, animate=True, empty=False):
    """Mesma regra do componente Heightmap: acima do relevo = dot, crista = accent, profundidade = opacidade."""
    k = min(px, py) / 10
    out = []
    for y in range(rows):
        for x, hgt in enumerate(heights):
            t = hgt - (rows - 1 - y)
            cx, cy = x0 + x * px + px / 2, y0 + y * py + py / 2
            if empty or t < 0:
                out.append(f'<circle cx="{f2(cx)}" cy="{f2(cy)}" r="{f2(1 * k)}" fill="{d.c("dot")}"/>')
                continue
            if t < 1.1:
                r, o = 4.5, 1
            else:
                r, o = max(1.5, 4 - t * 0.35), max(0.16, 0.7 - t * 0.07)
            anim = ""
            out.append(f'<circle cx="{f2(cx)}" cy="{f2(cy)}" r="{f2(r * k)}" fill="{d.c("accent")}" fill-opacity="{f2(o)}"{anim}/>')
    d.add("<g>" + "".join(out) + "</g>")


def peak_ring(d, cx, cy, r=8):
    d.add(f'<circle cx="{f2(cx)}" cy="{f2(cy)}" r="{r}" fill="none" stroke="{d.c("signal")}" stroke-width="2"/>')


# ---------------------------------------------------------------- blocos
def hero(C, theme):
    """Sessão de terminal estática: hello, a saudação, topics com cursor piscando."""
    P = 32
    cmds = C.get("commands", ["hello", "topics"])
    d = Doc(W, 10, theme, f"{C['prompt']}:~$ {cmds[0]} — {C['hello']} {C['lead']}", bg="bg")

    def prompt(top, cmd):
        y = baseline(top, 15, 24, "mono400")
        x = P + d.text(P, y, C["prompt"], "mono400", 15, d.c("accent"))
        x += d.text(x, y, ":~$", "mono400", 15, d.c("muted")) + 10
        return x + d.text(x, y, cmd, "mono400", 15, d.c("text-strong"))

    # top bar
    bw = d.text(P, baseline(0, 15, 56, "mono500"), C["brand"], "mono500", 15, d.c("text-strong"))
    d.rect(P + bw + 3, 20, 9, 16, fill=d.c("accent"))
    label(d, W - P, 20, C["location"], anchor="end")
    d.hline(1, W - 1, 56, d.c("line"))

    top = 56 + 40
    prompt(top, cmds[0])
    top += 24 + 14
    size = 72
    while FONT["sans200"].width(C["hello"], size, -0.035) > W - 2 * P and size > 40:
        size -= 2
    d.text(P, baseline(top, size, size * 1.02, "sans200"), C["hello"], "sans200", size, d.c("text-strong"),
           extra=' letter-spacing="-0.035em"')
    top += size * 1.02 + 12
    for line in wrap(C["lead"], "sans300", 19, 680):
        d.text(P, baseline(top, 19, 30, "sans300"), line, "sans300", 19, d.c("text"))
        top += 30
    top += 28
    prompt(top, cmds[1])
    top += 24 + 12
    x = P
    for t in C.get("topics", []):
        x += tag(d, x, top, t, "neutral", dot=False) + 8
    top += 26 + 20
    end = prompt(top, "")
    d.rect(end, top + 4, 9, 16, fill=d.c("accent"), extra=' class="blink"')
    top += 24 + 40

    d.h = top
    d.els.insert(0, f'<rect x="0.5" y="0.5" width="{W - 1}" height="{f2(d.h - 1)}" fill="none" stroke="{d.c("line-strong")}" stroke-width="1"/>')
    d.els.insert(1, f'<rect x="0" y="0" width="12" height="2" fill="{d.c("accent")}"/>')
    d.els.insert(2, f'<rect x="0" y="0" width="2" height="12" fill="{d.c("accent")}"/>')
    return d


def section(theme, addr, title, aside=""):
    H = 88
    d = Doc(W, H, theme, f"{addr} · {title}")
    base = 64
    aw = d.text(0, base - 12, addr, "mono400", 13, d.c("accent"), 0.16)
    d.text(aw + 20, base, title, "sans200", 44, d.c("text-strong"), extra=' letter-spacing="-0.02em"')
    if aside:
        d.text(W, base - 4, aside, "mono400", 12, d.c("muted"), 0.14, anchor="end")
    d.hline(0, W, H - 1, d.c("line"))
    return d


def ellipsize(s, font, size, maxw, ls=0.0):
    if FONT[font].width(s, size, ls) <= maxw:
        return s
    while s and FONT[font].width(s + "…", size, ls) > maxw:
        s = s[:-1]
    return s.rstrip() + "…"


PIN_GAP = 16


def pinned_row(r, i, theme):
    """Linha de repositório fixado: largura total, descrição inteira, meta numa linha só."""
    P = 24
    desc = r["description"] or ""
    lines = wrap(desc, "sans400", 14, W - 2 * P) if desc else []
    h = 18 + 28 + 6 + max(1, len(lines)) * 22 + 18
    d = Doc(W, h + PIN_GAP, theme, f"{r['name']}. {desc}".strip())
    d.rect(0.5, 0.5, W - 1, h - 1, fill=d.c("surface"), stroke=d.c("line-strong"))
    d.corner(0, 0)
    top = 18
    y = baseline(top, 22, 28, "sans300")
    x = P + d.text(P, baseline(top, 12, 28, "mono400"), f"R/{i:02d}", "mono400", 12, d.c("muted"), 0.14) + 16
    # meta à direita, montada da direita para a esquerda
    fork = "FORK" if r["forks"] == 1 else "FORKS"
    est = "ESTRELA" if r["stars"] == 1 else "ESTRELAS"
    parts = [f"{r['stars']} {est}", f"{r['forks']} {fork}", f"ATUALIZADO {r['pushed']}"]
    if r["language"]:
        parts.insert(0, r["language"].upper())
    meta = " · ".join(parts)
    mw = FONT["mono400"].width(meta, 11, 0.12)
    mx = W - P - mw
    my = baseline(top, 11, 28, "mono400")
    if r["language"]:
        lw = FONT["mono400"].width(parts[0], 11, 0.12)
        d.add(f'<circle cx="{f2(mx - 11)}" cy="{f2(top + 14)}" r="3.5" fill="{d.c("accent")}"/>')
        d.text(mx, my, parts[0], "mono400", 11, d.c("text"), 0.12)
        rest = " · " + " · ".join(parts[1:])
        d.text(mx + lw + 0.12 * 11, my, rest, "mono400", 11, d.c("muted"), 0.12)
        mx -= 17
    else:
        d.text(mx, my, meta, "mono400", 11, d.c("muted"), 0.12)
    size = 22
    name = r["name"]
    while FONT["sans300"].width(name, size, -0.01) > mx - x - 24 and size > 15:
        size -= 1
    d.text(x, y, name, "sans300", size, d.c("text-strong"), extra=' letter-spacing="-0.01em"')
    top += 28 + 6
    if lines:
        for line in lines:
            d.text(P, baseline(top, 14, 22, "sans400"), line, "sans400", 14, d.c("text"))
            top += 22
    else:
        d.text(P, baseline(top, 12, 22, "mono400"), "[SEM DESCRIÇÃO]", "mono400", 12, d.c("muted"), 0.08)
    return d


def button(theme, text, variant):
    F = FONT["mono500" if variant == "primary" else "mono400"]
    w = round(F.width(text, 12, 0.12) + 40)
    d = Doc(w, 44, theme, text.replace("→", "").strip())
    font = F.key
    if variant == "primary":
        d.rect(0, 0, w, 44, fill=d.c("accent"))
        col = d.c("on-accent")
    else:
        d.rect(0.5, 0.5, w - 1, 43, stroke=d.c("control"))
        col = d.c("text")
    d.text(20, baseline(0, 12, 44, font), text, font, 12, col, 0.12)
    return d


def footer(C, theme):
    d = Doc(W, 72, theme, f"{C['brand']} · {C['revision']}")
    d.hline(0, W, 0, d.c("line"))
    bw = d.text(0, baseline(16, 15, 40, "mono500"), C["brand"], "mono500", 15, d.c("text-strong"))
    d.rect(bw + 3, 28, 9, 16, fill=d.c("accent"), extra=' class="blink"')
    label(d, W, 28, f"{C['revision']} · SISTEMA VISUAL GABRIEL.S", size=11, anchor="end")
    return d


# ---------------------------------------------------------------- atividade
GQL = """query($login:String!){user(login:$login){
 pinnedItems(first:6,types:REPOSITORY){nodes{... on Repository{name owner{login} description url
  stargazerCount forkCount pushedAt primaryLanguage{name}}}}
 contributionsCollection{contributionCalendar{totalContributions weeks{contributionDays{date contributionCount}}}}
 repositories(first:100,ownerAffiliations:OWNER,isFork:false,privacy:PUBLIC){totalCount nodes{stargazerCount
  languages(first:10,orderBy:{field:SIZE,direction:DESC}){edges{size node{name}}}}}}}"""


def fetch_stats(login, ignore):
    token = os.environ.get("GH_STATS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": GQL, "variables": {"login": login}}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json", "User-Agent": "gs-readme"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if "errors" in payload:
        raise SystemExit(f"GraphQL: {payload['errors']}")
    u = payload["data"]["user"]
    cal = u["contributionsCollection"]["contributionCalendar"]
    days = [(dd["date"], dd["contributionCount"]) for w in cal["weeks"] for dd in w["contributionDays"]]
    langs = defaultdict(int)
    stars = 0
    for n in u["repositories"]["nodes"]:
        stars += n["stargazerCount"]
        for e in n["languages"]["edges"]:
            if e["node"]["name"] not in ignore:
                langs[e["node"]["name"]] += e["size"]
    pinned = [{"name": n["name"] if n["owner"]["login"] == login else f"{n['owner']['login']}/{n['name']}",
               "description": n["description"], "url": n["url"], "stars": n["stargazerCount"],
               "forks": n["forkCount"], "pushed": n["pushedAt"][:10],
               "language": (n["primaryLanguage"] or {}).get("name")} for n in u["pinnedItems"]["nodes"]]
    return {"days": days, "total": cal["totalContributions"], "repos": u["repositories"]["totalCount"],
            "stars": stars, "langs": dict(langs), "pinned": pinned}


def demo_stats():
    r = random.Random(0x13B)
    end = dt.date.today()
    start = end - dt.timedelta(days=370)
    start -= dt.timedelta(days=(start.weekday() + 1) % 7)
    days, d0 = [], start
    while d0 <= end:
        wk = (d0 - start).days / 7
        base = 2.2 + 2 * math.sin(wk / 6) + 1.5 * math.sin(wk / 2.3)
        days.append((d0.isoformat(), max(0, int(r.gauss(base, 2.5))) if r.random() > 0.3 else 0))
        d0 += dt.timedelta(days=1)
    return {"days": days, "total": sum(c for _, c in days), "repos": 14, "stars": 23,
            "langs": {"Python": 410000, "R": 160000, "C++": 120000, "GLSL": 50000, "TypeScript": 80000, "GDScript": 30000},
            "pinned": [{"name": n, "description": dsc, "url": "https://github.com/gabriellsar", "stars": st,
                        "forks": 0, "pushed": "2026-11-10", "language": lg} for n, dsc, st, lg in [
                ("demo-repo-um", "Descrição de exemplo para pré-visualizar o card.", 3, "Python"),
                ("demo-repo-dois", None, 0, None),
                ("um-nome-de-repositorio-bem-comprido", "Texto longo de exemplo que precisa quebrar em várias linhas para testar o limite de três linhas e as reticências no final do card.", 12, "TypeScript")]]}


MONTHS = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]


def activity(stats, theme, today):
    empty = stats is None
    d = Doc(W, 10, theme, "Atividade no GitHub")
    # --- stats
    if empty:
        vals = [("CONTRIBUIÇÕES", "[—]", None, "AGUARDANDO ACTION"), ("REPOSITÓRIOS", "[—]", None, "PÚBLICOS"),
                ("ESTRELAS", "[—]", None, "EM REPOS PÚBLICOS"), ("SEQUÊNCIA", "[—]", None, "DIAS SEGUIDOS")]
    else:
        counts = [c for _, c in stats["days"]]
        half = len(counts) // 2
        a, b = sum(counts[:half]), sum(counts[half:])
        delta = None
        if a > 0:
            pct = round((b - a) / a * 100)
            delta = ("up" if pct > 0 else "down" if pct < 0 else "flat", f"{pct:+d}%".replace("-", "−"))
        streak, i = 0, len(counts) - 1
        if counts and counts[-1] == 0:
            i -= 1
        while i >= 0 and counts[i] > 0:
            streak += 1
            i -= 1
        fmt = lambda n: f"{n:,}".replace(",", ".")
        vals = [("CONTRIBUIÇÕES", fmt(stats["total"]), delta, "VS 6M ANT." if delta else "12 MESES"),
                ("REPOSITÓRIOS", fmt(stats["repos"]), None, "PÚBLICOS"),
                ("ESTRELAS", fmt(stats["stars"]), None, "EM REPOS PÚBLICOS"),
                ("SEQUÊNCIA", fmt(streak), None, "DIAS SEGUIDOS")]
    gap = 16
    sw = (W - 3 * gap) / 4
    for i, (lab, val, delta, note) in enumerate(vals):
        x = i * (sw + gap)
        d.rect(x + 0.5, 0.5, sw - 1, 131, fill=d.c("surface"), stroke=d.c("line-strong"))
        if i == 0:
            d.corner(x, 0)
        label(d, x + 24, 24, lab)
        d.text(x + 24, baseline(48, 32, 36, "mono400"), val, "mono400", 32, d.c("text-strong"),
               extra=' letter-spacing="-0.02em"')
        fx = x + 24
        if delta:
            tone = {"up": "accent", "down": "signal", "flat": "muted"}[delta[0]]
            glyph = {"up": "▲", "down": "▼", "flat": "—"}[delta[0]]
            fx += d.text(fx, baseline(92, 12, 16, "mono400"), f"{glyph} {delta[1]}", "mono400", 12, d.c(tone)) + 12
        d.text(fx, baseline(92, 12, 16, "mono400"), note, "mono400", 12, d.c("muted"))
    top = 132 + 32

    # --- relevo de contribuições
    d.hline(0, W, top, d.c("line"))
    top += 16
    if empty:
        weeks = [0] * 53
        start_dates = []
    else:
        weeks, start_dates = [], []
        for i in range(0, len(stats["days"]), 7):
            chunk = stats["days"][i:i + 7]
            weeks.append(sum(c for _, c in chunk))
            start_dates.append(chunk[0][0])
    n = len(weeks)
    label(d, 0, top, f"FIG.01 — RELEVO DE CONTRIBUIÇÕES · {n} SEMANAS", size=11)
    label(d, W, top, "SEM DADOS" if empty else f"ATUALIZADO {today}", size=11, anchor="end")
    top += 16 + 12
    rows = 11
    gx0 = 12
    px = (W - 2 * gx0) / n
    py = px * 0.8
    mx = max(weeks) or 1
    raw = [w / mx for w in weeks]
    sm = [(raw[max(0, i - 1)] + 2 * raw[i] + raw[min(n - 1, i + 1)]) / 4 for i in range(n)]
    hs = [0.35 + (rows - 1.6) * (0.65 * math.sqrt(s) + 0.35 * math.sqrt(r_)) for s, r_ in zip(sm, raw)]
    terrain_dots(d, gx0, top, hs, rows, px, py, empty=empty)
    if not empty and max(weeks) > 0:
        pk = max(range(n), key=lambda i: weeks[i])
        ph = math.floor(hs[pk])
        peak_ring(d, gx0 + pk * px + px / 2, top + (rows - 1 - ph) * py + py / 2, r=8 * px / 10 + 1)
    top += rows * py + 10
    if start_dates:
        last = None
        for i, sd in enumerate(start_dates):
            m = int(sd[5:7])
            if m != last and i < n - 2:
                d.text(gx0 + i * px + px / 2, baseline(top, 10, 14, "mono400"), MONTHS[m - 1], "mono400", 10,
                       d.c("muted"), 0.1)
                last = m
    top += 14 + 10
    if not empty and max(weeks) > 0:
        y, mo, da = start_dates[pk].split("-")
        label(d, 0, top, f"PICO · SEMANA DE {da}/{mo}/{y} · {weeks[pk]} CONTRIBUIÇÕES", color=d.c("signal"),
              size=11, ls=0.12)
    else:
        label(d, 0, top, "[SEM PICO]", size=11, ls=0.12)
    label(d, W, top, "1 COLUNA = 1 SEMANA", size=11, anchor="end")
    top += 16 + 16
    d.hline(0, W, top, d.c("line"))
    top += 32

    # --- linguagens
    label(d, 0, top, "LINGUAGENS · BYTES EM REPOS PÚBLICOS")
    top += 16 + 12
    segs, sg = 60, 3
    segw = (W - (segs - 1) * sg) / segs
    items = []
    if not empty and stats["langs"]:
        tot = sum(stats["langs"].values())
        srt = sorted(stats["langs"].items(), key=lambda kv: -kv[1])
        top4, rest = srt[:4], sum(v for _, v in srt[4:])
        items = [(k, v / tot) for k, v in top4] + ([("Outros", rest / tot)] if rest else [])
    # distribui segmentos por maior resto
    alloc = [int(p * segs) for _, p in items]
    order = sorted(range(len(items)), key=lambda i: -(items[i][1] * segs - alloc[i]))
    for i in order[: segs - sum(alloc)]:
        alloc[i] += 1
    colors = []
    for i, a_ in enumerate(alloc):
        colors += [d.t["data"][i]] * a_
    colors += [d.c("line-strong")] * (segs - len(colors))
    for i, col in enumerate(colors):
        d.rect(i * (segw + sg), top, segw, 8, fill=col)
    top += 8 + 16
    x = 0
    if items:
        for i, (k, p) in enumerate(items):
            d.add(f'<circle cx="{f2(x + 3.5)}" cy="{f2(top + 8)}" r="3.5" fill="{d.t["data"][i]}"/>')
            x += 15
            x += d.text(x, baseline(top, 11, 16, "mono400"), k.upper(), "mono400", 11, d.c("text"), 0.14) + 8
            x += d.text(x, baseline(top, 11, 16, "mono400"), f"{p * 100:.0f}%", "mono400", 11, d.c("muted"), 0.04) + 24
    else:
        label(d, 0, top, "[SEM DADOS]", size=11)
    d.h = top + 16 + 4
    return d


# ---------------------------------------------------------------- README
def pic(name, alt, attr='width="100%"'):
    return (f'<picture><source media="(prefers-color-scheme: light)" srcset="assets/{name}-light.svg">'
            f'<img src="assets/{name}-dark.svg" {attr} alt="{esc(alt)}"></picture>')


def readme(C, pinned):
    out = ["<!-- Gerado por scripts/build.py a partir de content.json. Não edite à mão: a Action reescreve. -->", ""]
    out.append(f'<a href="{C["links"][0][1]}">' + pic("hero", f"{C['prompt']}:~$ {C.get('commands', ['hello'])[0]} — {C['hello']} {C['lead']}") + "</a>")
    out += ["", "<br><br>", ""]
    n = 0
    if pinned:
        n += 1
        out += [pic(f"section-{n}", f"0x{n:02d} · Repositórios fixados"), "", "<br>", ""]
        for i, r in enumerate(pinned, 1):
            alt = f"{r['name']}" + (f" — {r['description']}" if r["description"] else "")
            out.append(f'<a href="{r["url"]}">' + pic(f"pinned-{i}", alt) + "</a>")
        out += ["", "<br><br>", ""]
    n += 1
    out += [pic(f"section-{n}", f"0x{n:02d} · Atividade"), "", "<br>", "",
            pic("activity", "Atividade no GitHub nos últimos 12 meses: contribuições, repositórios, estrelas, "
                            "sequência, relevo de contribuições por semana e linguagens. Atualizado todo dia."),
            "", "<br><br><br>", ""]
    n += 1
    out += [pic(f"section-{n}", f"0x{n:02d} · Contato"), "", "<br>", ""]
    for i, (t, href, _) in enumerate(C["links"], 1):
        out.append(f'<a href="{href}">' + pic(f"link-{i}", t.replace("→", "").strip().capitalize(), 'height="44"') + "</a>")
    out += ["", "<br><br><br>", "", pic("footer", f"{C['brand']} · {C['revision']}"), ""]
    return "\n".join(out)


# ---------------------------------------------------------------- main
def write(name, doc):
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / name).write_text(doc.render(getattr(doc, "css", "")), encoding="utf-8")


def main():
    C = json.loads((ROOT / "content.json").read_text(encoding="utf-8"))
    demo = "--demo" in sys.argv
    today = dt.date.today().isoformat()
    stats = demo_stats() if demo else fetch_stats(C["login"], set(C.get("ignore_languages", [])))
    pinned = (stats or {}).get("pinned") or []
    secs = ([("Repositórios fixados", f"{len(pinned):02d} REPOS")] if pinned else []) + [
        ("Atividade", "GITHUB · 12 MESES"), ("Contato", C["location"])]
    for old in ASSETS.glob("*.svg"):
        old.unlink()
    for th in THEMES:
        write(f"hero-{th}.svg", hero(C, th))
        for i, (t, s) in enumerate(secs, 1):
            write(f"section-{i}-{th}.svg", section(th, f"0x{i:02d}", t, s))
        for i, r in enumerate(pinned, 1):
            write(f"pinned-{i}-{th}.svg", pinned_row(r, i, th))
        write(f"activity-{th}.svg", activity(stats, th, today))
        for i, (t, _, v) in enumerate(C["links"], 1):
            write(f"link-{i}-{th}.svg", button(th, t, v))
        write(f"footer-{th}.svg", footer(C, th))
    (ROOT / "README.md").write_text(readme(C, pinned), encoding="utf-8")
    print("ok" + (" (demo)" if demo else "" if stats else " (sem token: fixados e atividade ficam vazios)"))


if __name__ == "__main__":
    main()

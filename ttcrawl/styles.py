"""Computed styles from the browser, folded into one picture of the brand."""
import re
from collections import Counter


def normalize_color(value):
    """'rgb(47, 91, 234)' / 'rgba(47, 91, 234, 0.5)' / '#2F5BEA' -> '#2f5bea'
    (an alpha under 1 is kept as a suffix)."""
    if not value:
        return None
    v = value.strip().lower()
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)", v)
    if m:
        r, g, b = (int(m.group(i)) for i in (1, 2, 3))
        alpha = m.group(4)
        hexed = "#%02x%02x%02x" % (r, g, b)
        if alpha is not None and float(alpha) < 1:
            return "%s@%s" % (hexed, alpha)
        return hexed
    m = re.match(r"#([0-9a-f]{3})$", v)
    if m:
        return "#" + "".join(ch * 2 for ch in m.group(1))
    m = re.match(r"#([0-9a-f]{6})", v)
    if m:
        return "#" + m.group(1)
    return None


def family_names(font_family):
    """'"Bricolage Grotesque", Inter, sans-serif' -> the family names in order."""
    return [f.strip().strip("'\"") for f in (font_family or "").split(",") if f.strip()]


def merge_styles(per_page):
    """Fold the browser's per-page readings into one picture: fonts by use,
    colours by count, type by role, buttons, logo candidates."""
    fonts, colors, roles, buttons, logos = Counter(), Counter(), {}, [], []
    for s in per_page:
        if not s:
            continue
        for f in s.get("fonts") or []:
            for name in family_names(f)[:1]:
                fonts[name] += 1
        for value, n in s.get("colors") or []:
            c = normalize_color(value)
            if c:
                colors[c] += int(n)
        for key in ("body", "h1", "h2", "h3", "p", "a"):
            if s.get(key) and key not in roles:
                r = dict(s[key])
                r["font"] = family_names(r.get("font"))[:1][0] if r.get("font") else ""
                r["color"] = normalize_color(r.get("color"))
                r["background"] = normalize_color(r.get("background"))
                roles[key] = r
        for b in s.get("buttons") or []:
            b = dict(b)
            b["background"], b["color"] = normalize_color(b.get("background")), normalize_color(b.get("color"))
            b["font"] = family_names(b.get("font"))[:1][0] if b.get("font") else ""
            if b not in buttons:
                buttons.append(b)
        for l in s.get("logos") or []:
            if l not in logos:
                logos.append(l)
    return {
        "fonts": [f for f, _ in fonts.most_common(8)],
        "colors": [{"color": c, "count": n} for c, n in colors.most_common(16)],
        "roles": roles,
        "buttons": buttons[:8],
        "logos": logos[:6],
        "pages_read": sum(1 for s in per_page if s),
    }

"""robots.txt con comodines (* y $), grupos repetidos y Crawl-delay (RFC 9309).

urllib.robotparser no interpreta comodines: con él, reglas como
`Disallow: /items/*/full` nunca coincidirían.
"""

import re
from urllib.parse import urlsplit


class Robots:
    def __init__(self, text, agent):
        groups, current, after_agent = [], None, False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                # Varias líneas User-agent seguidas comparten un mismo grupo.
                if current is None or not after_agent:
                    current = {"agents": [], "rules": [], "delay": None}
                    groups.append(current)
                current["agents"].append(value.lower())
                after_agent = True
                continue
            after_agent = False
            if current is None:
                continue
            if key in ("allow", "disallow") and value:
                current["rules"].append((key == "allow", value, compile_rule(value)))
            elif key == "crawl-delay":
                try:
                    current["delay"] = float(value)
                except ValueError:
                    pass
        token = agent.lower()
        mine = [g for g in groups if any(a != "*" and a in token for a in g["agents"])]
        # Sin grupo propio se aplican todos los grupos `*` (algunos sitios repiten el grupo).
        selected = mine or [g for g in groups if "*" in g["agents"]]
        self.rules = [rule for group in selected for rule in group["rules"]]
        delays = [group["delay"] for group in selected if group["delay"] is not None]
        self.crawl_delay = max(delays) if delays else None

    @classmethod
    def allow_all(cls):
        return cls("", "*")

    def allowed(self, url):
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best = None
        for allow, pattern, regex in self.rules:
            if regex.match(target):
                # La regla más específica gana; en empate, Allow.
                key = (len(pattern), allow)
                if best is None or key > best:
                    best = key
        return True if best is None else best[1]


def compile_rule(pattern):
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if char == "*" else re.escape(char) for char in body)
    return re.compile(regex + ("$" if anchored else ""))

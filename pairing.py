"""
Pareamento automático para adicionar vários filmes de uma vez.

Recebe uma pilha de arquivos (dublados e originais misturados) e descobre quem é par de quem.
Os nomes nem sempre batem (dublado costuma vir com título em português: "O.Cao.e.a.Raposa"
x "The.Fox.and.the.Hound"), então a nota combina: semelhança do título, ano e duração
(a dublagem PAL é ~4% mais curta). Dentro do par, o de maior resolução é o original.
"""

import os
import re
import unicodedata
from difflib import SequenceMatcher

import naming

JUNK = r"\b(dublado|dublada|legendado|nacional|dual|audio|multi|ptbr|pt|br|portugues|completo|torrent|www|com)\b"


def norm_title(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    t = re.sub(JUNK, " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def describe(path, info, height, duration):
    p = naming.parse_name(path)
    return {"path": path, "title": norm_title(p["title"] or ""), "year": p["year"], "episode": p["episode"],
            "height": height or 0, "dur": duration or 0, "size": os.path.getsize(path)}


def score(a, b):
    s = 2 * SequenceMatcher(None, a["title"], b["title"]).ratio()
    if a["year"] and b["year"]:
        s += 1 if a["year"] == b["year"] else -2
    if a["episode"] or b["episode"]:
        s += 1 if a["episode"] == b["episode"] else -3
    if a["dur"] and b["dur"]:
        r = min(a["dur"], b["dur"]) / max(a["dur"], b["dur"])
        s += 1 if r > 0.94 else (0 if r > 0.85 else -2)
    if a["height"] == b["height"] and abs(a["size"] - b["size"]) < 1024:
        s -= 3   # parece o mesmo arquivo duas vezes
    return s


def pair(items, threshold=1.5):
    """items: lista de describe(). Retorna ([(dublado, original, nota)], [sobras])."""
    cands = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            cands.append((score(items[i], items[j]), i, j))
    cands.sort(reverse=True)
    used, pairs = set(), []
    for s, i, j in cands:
        if s < threshold or i in used or j in used:
            continue
        a, b = items[i], items[j]
        orig, dub = (a, b) if (a["height"], a["size"]) > (b["height"], b["size"]) else (b, a)
        pairs.append((dub["path"], orig["path"], s))
        used.update((i, j))
    rest = [it["path"] for k, it in enumerate(items) if k not in used]
    return pairs, rest

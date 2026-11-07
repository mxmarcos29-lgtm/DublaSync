"""
Gerador de arquivo .torrent (BitTorrent v1, o formato que todo cliente aceita).

Não depende de nada além do Python: faz o bencode e o SHA-1 de cada pedaço do arquivo.
O SHA-1 do hashlib solta o GIL, então os pedaços são verificados em vários núcleos.
"""

import hashlib
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# trackers públicos antigos e estáveis (o botão "Atualizar" baixa a lista do dia)
DEFAULT_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
]
TRACKERS_URL = "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt"


def fetch_best_trackers(timeout=10):
    req = urllib.request.Request(TRACKERS_URL, headers={"User-Agent": "DublaSync"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", errors="ignore")
    return [l.strip() for l in text.splitlines() if l.strip().startswith(("udp://", "http://", "https://"))]


def bencode(x):
    if isinstance(x, bool):
        x = int(x)
    if isinstance(x, int):
        return b"i%de" % x
    if isinstance(x, str):
        x = x.encode("utf-8")
    if isinstance(x, (bytes, bytearray)):
        return b"%d:%s" % (len(x), bytes(x))
    if isinstance(x, (list, tuple)):
        return b"l" + b"".join(bencode(i) for i in x) + b"e"
    if isinstance(x, dict):
        items = sorted(((k.encode("utf-8") if isinstance(k, str) else k), v) for k, v in x.items())
        return b"d" + b"".join(bencode(k) + bencode(v) for k, v in items) + b"e"
    raise TypeError(type(x))


def piece_size_for(total):
    """~1000–2000 pedaços, entre 256 KiB e 16 MiB (o que os sites costumam exigir)."""
    ps = 256 * 1024
    while total / ps > 2000 and ps < 16 * 1024 * 1024:
        ps *= 2
    return ps


def make_torrent(path, trackers, private=False, source="", comment="", out_path=None,
                 progress=lambda f: None, check=lambda: None, threads=4):
    """Cria <arquivo>.torrent ao lado do vídeo. Retorna (caminho_torrent, magnet, info_hash)."""
    total = os.path.getsize(path)
    ps = piece_size_for(total)
    n_pieces = (total + ps - 1) // ps
    hashes = [None] * n_pieces
    done = 0
    pending = []
    with open(path, "rb") as f, ThreadPoolExecutor(threads) as pool:
        for i in range(n_pieces):
            data = f.read(ps)
            pending.append((i, len(data), pool.submit(hashlib.sha1, data)))
            if len(pending) >= threads * 6:          # leitura e hash em paralelo
                j, n, fut = pending.pop(0)
                hashes[j] = fut.result().digest()
                done += n
                if j % 16 == 0:
                    check()
                    progress(done / max(total, 1))
        for j, n, fut in pending:
            hashes[j] = fut.result().digest()
            done += n
    progress(1.0)
    info = {"name": os.path.basename(path), "length": total, "piece length": ps, "pieces": b"".join(hashes)}
    if private:
        info["private"] = 1
    if source:
        info["source"] = source
    trackers = [t.strip() for t in trackers if t.strip()]
    meta = {"info": info, "created by": "DublaSync", "creation date": int(time.time())}
    if trackers:
        meta["announce"] = trackers[0]
        meta["announce-list"] = [[t] for t in trackers]
    if comment:
        meta["comment"] = comment
    raw = bencode(meta)
    info_hash = hashlib.sha1(bencode(info)).hexdigest()
    out_path = out_path or (path + ".torrent" if not path.lower().endswith(".mkv")
                            else os.path.splitext(path)[0] + ".torrent")
    with open(out_path, "wb") as fo:
        fo.write(raw)
    magnet = "magnet:?xt=urn:btih:" + info_hash + "&dn=" + urllib.parse.quote(info["name"])
    if not private:
        magnet += "".join("&tr=" + urllib.parse.quote(t, safe="") for t in trackers[:10])
    return out_path, magnet, info_hash


# ----------------------------------------------------------- leitura (testes)
def bdecode(b, i=0):
    c = b[i:i + 1]
    if c == b"i":
        j = b.index(b"e", i)
        return int(b[i + 1:j]), j + 1
    if c == b"l":
        i += 1
        out = []
        while b[i:i + 1] != b"e":
            v, i = bdecode(b, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while b[i:i + 1] != b"e":
            k, i = bdecode(b, i)
            v, i = bdecode(b, i)
            out[k] = v
        return out, i + 1
    j = b.index(b":", i)
    n = int(b[i:j])
    return b[j + 1:j + 1 + n], j + 1 + n

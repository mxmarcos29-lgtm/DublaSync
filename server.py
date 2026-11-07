"""
DublaSync Servidor — versão web para rodar no NAS (ZimaOS/Docker).

Só usa a biblioteca padrão do Python + numpy. Abre uma página web (porta 8282) com
Novo trabalho, Fila, Histórico, Compartilhar e Configurações. O processamento roda no
próprio servidor, então o filme nunca passa pela rede.

Variáveis de ambiente:
  DUBLASYNC_HOME   pasta do programa no NAS (dados, temporários e logs ficam aqui)
  DUBLASYNC_ROOTS  pastas que o navegador de arquivos mostra (separadas por ':'), padrão /media:/DATA
  DUBLASYNC_PORT   porta da página (padrão 8282)
"""

import datetime as dt
import http.cookiejar
import json
import mimetypes
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
HOME = os.environ.get("DUBLASYNC_HOME") or HERE
DATA = os.path.join(HOME, "dados")
TEMP = os.path.join(HOME, "temp")
os.makedirs(DATA, exist_ok=True)
os.makedirs(TEMP, exist_ok=True)
tempfile.tempdir = TEMP          # arquivos temporários grandes ficam no disco grande, não no sistema

import dublasync_core as core  # noqa: E402
import naming  # noqa: E402
import pairing  # noqa: E402
import torrent as tor  # noqa: E402

VERSION = "1.7-servidor"


def git_version():
    """Data/código da versão baixada do GitHub (para saber quando atualizou)."""
    try:
        import subprocess
        r = subprocess.run(["git", "-C", HERE, "log", "-1", "--format=%h · %cd", "--date=format:%d/%m/%Y %H:%M"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


GIT_VERSION = git_version()
PORT = int(os.environ.get("DUBLASYNC_PORT", "8282"))
ROOTS = [r for r in os.environ.get("DUBLASYNC_ROOTS", "/media:/DATA").split(":") if r and os.path.isdir(r)] or [HOME]
MEDIA_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".flv", ".webm", ".ts", ".m2ts", ".mts", ".mpg",
             ".mpeg", ".vob", ".3gp", ".ogv", ".rmvb", ".rm", ".divx", ".asf", ".mka", ".mp3", ".aac", ".ac3",
             ".eac3", ".dts", ".flac", ".wav", ".m4a", ".ogg", ".opus", ".wma", ".thd", ".mlp"}

DEFAULT_CFG = {"lang": "Português", "codec": "ac3", "style": "cena", "dub_default": True,
               "remove_old": False, "folder": "", "torrent": True, "private": False,
               "trackers": list(tor.DEFAULT_TRACKERS), "source": "", "fast": True,
               "qb_enabled": False, "qb_url": "http://192.168.0.14:8181", "qb_user": "admin", "qb_pass": "",
               "qb_from": "/media/HD 8TB", "qb_to": "/HD 8TB", "qb_category": "", "qb_skip_check": False}


def load(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def save(name, obj):
    p = os.path.join(DATA, name)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)


def log_line(msg):
    try:
        with open(os.path.join(DATA, "registro.log"), "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except OSError:
        pass


def allowed(path):
    rp = os.path.realpath(path)
    return any(rp == os.path.realpath(r) or rp.startswith(os.path.realpath(r) + os.sep) for r in ROOTS + [HOME])


# ------------------------------------------------------------------ qBittorrent
class QB:
    """Cliente mínimo da API Web do qBittorrent."""

    def __init__(self, cfg):
        self.url = cfg["qb_url"].rstrip("/")
        self.user, self.pw = cfg["qb_user"], cfg["qb_pass"]
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _req(self, path, data=None, headers=None):
        h = {"Referer": self.url, "Origin": self.url}
        h.update(headers or {})
        r = urllib.request.Request(self.url + path, data=data, headers=h)
        with self.op.open(r, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="ignore")

    def login(self):
        body = urllib.parse.urlencode({"username": self.user, "password": self.pw}).encode()
        r = self._req("/api/v2/auth/login", body, {"Content-Type": "application/x-www-form-urlencoded"})
        if "ok" not in r.lower():
            raise RuntimeError("usuário ou senha do qBittorrent recusados")
        return self._req("/api/v2/app/version")

    def add(self, torrent_path, savepath, category="", skip_check=False):
        self.login()
        bnd = uuid.uuid4().hex
        parts = []

        def field(name, value):
            parts.append(f'--{bnd}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())

        with open(torrent_path, "rb") as f:
            data = f.read()
        parts.append((f'--{bnd}\r\nContent-Disposition: form-data; name="torrents"; filename="'
                      f'{os.path.basename(torrent_path)}"\r\nContent-Type: application/x-bittorrent\r\n\r\n').encode()
                     + data + b"\r\n")
        field("savepath", savepath)
        field("skip_checking", "true" if skip_check else "false")
        field("paused", "false")
        field("stopped", "false")
        if category:
            field("category", category)
        parts.append(f"--{bnd}--\r\n".encode())
        r = self._req("/api/v2/torrents/add", b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={bnd}"})
        if "fail" in r.lower():
            raise RuntimeError("o qBittorrent recusou o torrent (talvez ele já esteja lá)")
        return r


def qb_path(cfg, path):
    """Converte o caminho que o DublaSync vê no caminho que o qBittorrent vê."""
    src, dst = cfg["qb_from"].rstrip("/"), cfg["qb_to"].rstrip("/")
    if src and (path == src or path.startswith(src + "/")):
        return dst + path[len(src):]
    return path


# ------------------------------------------------------------------- motor/fila
class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.cfg = dict(DEFAULT_CFG)
        self.cfg.update(load("config.json", {}))
        self.queue = load("fila.json", [])
        for j in self.queue:
            if j.get("status") == "run":
                j.update(status="wait", frac=0.0, detail="")
        self.history = load("historico.json", [])
        self.paused = True            # não começa sozinho ao ligar: o usuário clica "Iniciar"
        self.current = None
        self.rep = None
        self.stages, self.info, self.started = {}, {}, None
        self.last_result = None
        self.wake = threading.Event()
        self.ff_version = core.ffmpeg_version()
        self.soxr = core.has_soxr() if self.ff_version else False
        self._clean_temp()
        threading.Thread(target=self._worker, daemon=True).start()

    @staticmethod
    def _clean_temp():
        for d in os.listdir(TEMP):
            shutil.rmtree(os.path.join(TEMP, d), ignore_errors=True)

    def save_queue(self):
        save("fila.json", self.queue)

    def save_cfg(self):
        save("config.json", self.cfg)

    # --- estado para a página
    def state(self):
        with self.lock:
            cfg = dict(self.cfg)
            cfg["qb_pass"] = "••••" if cfg.get("qb_pass") else ""
            return {"version": VERSION, "queue": self.queue, "current": self.current["id"] if self.current else None,
                    "stages": self.stages, "info": self.info, "started": self.started, "paused": self.paused,
                    "cfg": cfg, "stage_names": core.STAGES, "now": time.time(),
                    "tools": {"ffmpeg": self.ff_version, "soxr": self.soxr, "threads": core.THREADS, "git": GIT_VERSION},
                    "history": self.history[:200], "last": self.last_result,
                    "langs": list(core.LANGS), "codecs": core.QUALITY, "styles": naming.STYLES}

    # --- fila
    def new_job(self, dub, orig, out):
        return {"id": uuid.uuid4().hex[:12], "dub": dub, "orig": orig, "out": out, "cfg": dict(self.cfg),
                "status": "wait", "frac": 0.0, "detail": "", "elapsed": 0,
                "added": dt.datetime.now().strftime("%d/%m %H:%M")}

    def validate(self, dub, orig, out):
        for p in (dub, orig):
            if not p or not os.path.isfile(p) or not allowed(p):
                raise ValueError(f"Arquivo não encontrado: {p}")
        if os.path.realpath(dub) == os.path.realpath(orig):
            raise ValueError("Você escolheu o mesmo arquivo nos dois lados.")
        if os.path.realpath(out) in (os.path.realpath(dub), os.path.realpath(orig)):
            raise ValueError("O arquivo novo não pode ter o mesmo nome de um dos arquivos de entrada.")
        if not allowed(os.path.dirname(out)) or not os.path.isdir(os.path.dirname(out)):
            raise ValueError("Pasta de saída inválida.")
        with self.lock:
            if any(os.path.realpath(j["out"]) == os.path.realpath(out) and j["status"] in ("wait", "run")
                   for j in self.queue):
                raise ValueError("Esse arquivo já está na fila com o mesmo nome de saída.")

    def enqueue(self, jobs, front=False, start=False):
        with self.lock:
            if front:
                pos = next((i for i, j in enumerate(self.queue) if j["status"] == "wait"), len(self.queue))
                for k, job in enumerate(jobs):
                    self.queue.insert(pos + k, job)
            else:
                self.queue.extend(jobs)
            self.save_queue()
            if start:
                self.paused = False
        self.wake.set()

    def action(self, act, jid=None, d=0):
        with self.lock:
            idx = next((i for i, j in enumerate(self.queue) if j["id"] == jid), None)
            if act == "start":
                self.paused = False
            elif act == "pause":
                self.paused = True
            elif act == "cancel" and self.current and self.rep:
                self.paused = True
                self.rep.cancel()
            elif act == "move" and idx is not None:
                k = idx + d
                if 0 <= k < len(self.queue) and "run" not in (self.queue[idx]["status"], self.queue[k]["status"]):
                    self.queue[idx], self.queue[k] = self.queue[k], self.queue[idx]
            elif act == "remove" and idx is not None and self.queue[idx]["status"] != "run":
                self.queue.pop(idx)
            elif act == "retry" and idx is not None and self.queue[idx]["status"] in ("err", "cancel"):
                self.queue[idx].update(status="wait", frac=0.0, detail="", error=None)
            elif act == "clear_done":
                self.queue = [j for j in self.queue if j["status"] in ("wait", "run")]
            self.save_queue()
        self.wake.set()

    # --- trabalho
    def _on_stage(self, key, st, detail, frac):
        with self.lock:
            s = self.stages.setdefault(key, {})
            now = time.time()
            if st == "run" and "t0" not in s:
                s["t0"] = now
            if st in ("ok", "err", "skip"):
                s["t1"] = now
                s.setdefault("t0", now)
            s["status"] = st
            if frac is not None:
                s["frac"] = frac
            if detail is not None:
                s["detail"] = detail
            if self.current:
                w = {"ler": 1, "orig": 10, "dub": 12, "sync": 6, "ajuste": 12, "gerar": 45, "conferir": 2,
                     "torrent": 10, "limpar": 2}
                tot = sum(w.values())
                self.current["frac"] = sum(w[k] * (1.0 if self.stages.get(k, {}).get("status") in ("ok", "skip")
                                                   else self.stages.get(k, {}).get("frac", 0)) for k in w) / tot
                if st == "run":
                    self.current["detail"] = dict(core.STAGES)[key] + (f" — {detail}" if detail else "")

    def _on_info(self, d):
        with self.lock:
            self.info.update(d)

    def _worker(self):
        while True:
            self.wake.wait(2)
            self.wake.clear()
            with self.lock:
                if self.paused or self.current:
                    continue
                job = next((j for j in self.queue if j["status"] == "wait"), None)
                if not job:
                    continue
                missing = [p for p in (job["dub"], job["orig"]) if not os.path.isfile(p)]
                if missing:
                    job.update(status="err", detail="arquivo não encontrado: " + os.path.basename(missing[0]))
                    self.save_queue()
                    self.wake.set()
                    continue
                self.current = job
                job.update(status="run", frac=0.0, detail="começando…", started=time.time(), error=None)
                self.stages, self.info, self.started = {}, {}, time.time()
                self.rep = core.Reporter(on_stage=self._on_stage, on_log=lambda m: log_line("   " + m),
                                         on_info=self._on_info)
                self.save_queue()
            self._run(job)

    def _run(self, job):
        cfg = job["cfg"]
        log_line(f"INÍCIO {job['out']}")
        topts = ({"trackers": cfg.get("trackers", []), "private": cfg.get("private", False),
                  "source": cfg.get("source", "")} if cfg.get("torrent") else None)
        err, res = None, None
        try:
            res = core.build_output(job["orig"], job["dub"], job["out"], lang=core.LANGS[cfg["lang"]],
                                    title=f"{cfg['lang']} (Dublado)", codec=cfg["codec"],
                                    dub_default=cfg["dub_default"], remove_old=cfg["remove_old"],
                                    rep=self.rep, torrent_opts=topts, fast=cfg.get("fast", True))
        except core.SyncError as e:
            err = str(e)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            log_line(traceback.format_exc())
        qb_note = None
        if res and res.get("torrent") and self.cfg.get("qb_enabled"):
            try:
                live = self.cfg
                QB(live).add(res["torrent"], qb_path(live, os.path.dirname(res["out"])),
                             live.get("qb_category", ""), live.get("qb_skip_check", False))
                qb_note = "enviado ao qBittorrent"
            except Exception as e:  # noqa: BLE001
                qb_note = f"não consegui enviar ao qBittorrent: {e}"
        with self.lock:
            elapsed = time.time() - job.get("started", time.time())
            job["elapsed"] = elapsed
            entry = {"when": dt.datetime.now().strftime("%d/%m/%Y %H:%M"), "name": os.path.basename(job["out"]),
                     "out": job["out"], "dub": os.path.basename(job["dub"]), "elapsed": elapsed}
            if err:
                st = "cancel" if err == "Cancelado." else "err"
                for k, s in self.stages.items():
                    if s.get("status") == "run":
                        s.update(status="err", detail="cancelado" if st == "cancel" else "interrompido",
                                 t1=time.time())
                job.update(status=st, detail=err.split("\n")[0][:200], error=err[-2000:])
                entry.update(status=st, detail=job["detail"])
                log_line(f"{'CANCELADO' if st == 'cancel' else 'ERRO'} {job['out']}: {err.splitlines()[0][:200]}")
            else:
                sync = f"{res['speed_text']} · {res['delay']:+.2f} s".replace(".", ",")
                sync = sync.replace("dublagem ", "").replace("original ", "").replace(" fps →", " →")
                job.update(status="ok", frac=1.0, out=res["out"], torrent=res.get("torrent"),
                           magnet=res.get("magnet"), detail=sync + (f" · {qb_note}" if qb_note else ""))
                entry.update(status="ok", size=os.path.getsize(res["out"]) if os.path.exists(res["out"]) else 0,
                             sync=sync, cuts=res["cuts"], torrent=res.get("torrent"), magnet=res.get("magnet"),
                             qb=qb_note)
                self.last_result = {"out": res["out"], "torrent": res.get("torrent"), "magnet": res.get("magnet"),
                                    "qb": qb_note}
                log_line(f"OK {res['out']} ({int(elapsed)} s) {qb_note or ''}")
            self.history.insert(0, entry)
            self.history = self.history[:500]
            save("historico.json", self.history)
            self.current, self.rep = None, None
            self.save_queue()
        self.wake.set()


ENG = None


# ------------------------------------------------------------------ utilidades
def browse(path):
    if not path:
        return {"path": "", "parent": None, "dirs": [{"name": r, "path": r} for r in ROOTS], "files": []}
    if not allowed(path) or not os.path.isdir(path):
        raise ValueError("Pasta não permitida ou inexistente.")
    dirs, files = [], []
    with os.scandir(path) as it:
        for e in it:
            if e.name.startswith("."):
                continue
            try:
                if e.is_dir():
                    dirs.append({"name": e.name, "path": e.path})
                elif e.is_file() and os.path.splitext(e.name)[1].lower() in MEDIA_EXT:
                    st = e.stat()
                    files.append({"name": e.name, "path": e.path, "size": st.st_size, "mtime": st.st_mtime})
            except OSError:
                pass
    dirs.sort(key=lambda d: d["name"].lower())
    files.sort(key=lambda f: f["name"].lower())
    parent = os.path.dirname(path.rstrip("/"))
    if path.rstrip("/") in [r.rstrip("/") for r in ROOTS] or not allowed(parent):
        parent = ""
    return {"path": path, "parent": parent, "dirs": dirs, "files": files}


def probe_info(path, cfg):
    if not allowed(path) or not os.path.isfile(path):
        raise ValueError("Arquivo não encontrado.")
    info = core.probe(path)
    out = {"desc": core.describe_file(info), "name": os.path.basename(path), "path": path}
    v = naming.video_info(info) or {}
    try:
        d = core.fmt_clock(core.duration_of(info))
    except core.SyncError:
        d = ""
    a = naming._main_audio(info)
    an, ach, atm = naming.audio_tag(a) if a else ("", "", False)
    out["card_orig"] = {"value": (v.get("res") or "Áudio") + (" " + " ".join(v.get("hdr")) if v.get("hdr") else ""),
                        "sub": f"{d} · {an}{' Atmos' if atm else ''} {ach} · {v.get('codec', '')}"}
    aud = core.audio_streams(info)
    if aud:
        idx = core.pick_dub_stream(info, core.LANGS[cfg["lang"]])
        an2, ach2, _ = naming.audio_tag(aud[idx])
        ext = os.path.splitext(path)[1].lstrip(".").upper()
        out["card_dub"] = {"value": f"{an2} {ach2}", "sub": f"{d} · {ext} · faixa {idx + 1} de {len(aud)}"}
    out["suggest"] = os.path.basename(core.default_output(path, info, core.LANGS[cfg["lang"]], cfg["style"]))
    out["height"] = core.video_height(info)
    return out


def make_out(orig, name, folder, cfg):
    if not name:
        name = os.path.basename(core.default_output(orig, None, core.LANGS[cfg["lang"]], cfg["style"]))
    if not name.lower().endswith(".mkv"):
        name += ".mkv"
    name = naming.safe(name[:-4]) + ".mkv"
    return os.path.join(folder or cfg.get("folder") or os.path.dirname(orig), name)


def pair_files(paths, cfg):
    items, infos = [], {}
    for f in paths:
        if not allowed(f) or not os.path.isfile(f):
            continue
        try:
            info = core.probe(f)
            try:
                d = core.duration_of(info)
            except core.SyncError:
                d = 0
            infos[f] = info
            items.append(pairing.describe(f, info, core.video_height(info), d))
        except core.SyncError:
            infos[f] = None
    pairs, rest = pairing.pair(items)
    rest += [f for f in paths if infos.get(f) is None]
    out = []
    for dub, orig, _ in pairs:
        o = core.default_output(orig, infos[orig], core.LANGS[cfg["lang"]], cfg["style"])
        if cfg.get("folder"):
            o = os.path.join(cfg["folder"], os.path.basename(o))
        out.append({"dub": dub, "orig": orig, "out": o})
    return {"pairs": out, "rest": rest}


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "DublaSync"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "static", "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path.startswith("/static/"):
                p = os.path.realpath(os.path.join(HERE, u.path.lstrip("/")))
                if p.startswith(os.path.join(HERE, "static")) and os.path.isfile(p):
                    with open(p, "rb") as f:
                        return self._send(200, f.read(), mimetypes.guess_type(p)[0] or "application/octet-stream")
                return self._send(404, {"error": "não encontrado"})
            if u.path == "/api/state":
                return self._send(200, ENG.state())
            if u.path == "/api/browse":
                return self._send(200, browse(q.get("path", "")))
            if u.path == "/api/torrent":
                p = q.get("path", "")
                if p.endswith(".torrent") and allowed(p) and os.path.isfile(p):
                    with open(p, "rb") as f:
                        fn = urllib.parse.quote(os.path.basename(p))
                        return self._send(200, f.read(), "application/x-bittorrent",
                                          {"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"})
                return self._send(404, {"error": "não encontrado"})
            if u.path == "/api/log":
                p = os.path.join(DATA, "registro.log")
                txt = open(p, encoding="utf-8").read()[-20000:] if os.path.exists(p) else ""
                return self._send(200, txt, "text/plain; charset=utf-8")
            return self._send(404, {"error": "não encontrado"})
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"error": str(e)})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            b = self._json()
            cfg = ENG.cfg
            if u.path == "/api/probe":
                return self._send(200, probe_info(b.get("path", ""), cfg))
            if u.path == "/api/enqueue":
                out = make_out(b["orig"], b.get("name", "").strip(), b.get("folder", "").strip(), cfg)
                ENG.validate(b["dub"], b["orig"], out)
                if os.path.exists(out) and not b.get("overwrite"):
                    return self._send(409, {"error": "exists", "out": out})
                job = ENG.new_job(b["dub"], b["orig"], out)
                ENG.enqueue([job], front=b.get("front", False), start=b.get("start", False))
                return self._send(200, {"ok": True, "job": job})
            if u.path == "/api/pair":
                return self._send(200, pair_files(b.get("paths", []), cfg))
            if u.path == "/api/enqueue_many":
                jobs, skipped = [], []
                for p in b.get("pairs", []):
                    try:
                        ENG.validate(p["dub"], p["orig"], p["out"])
                        jobs.append(ENG.new_job(p["dub"], p["orig"], p["out"]))
                    except ValueError as e:
                        skipped.append(f"{os.path.basename(p['out'])}: {e}")
                ENG.enqueue(jobs, start=b.get("start", False))
                return self._send(200, {"added": len(jobs), "skipped": skipped})
            if u.path == "/api/queue":
                ENG.action(b.get("action"), b.get("id"), int(b.get("dir", 0)))
                return self._send(200, {"ok": True})
            if u.path == "/api/settings":
                with ENG.lock:
                    for k, v in b.items():
                        if k in DEFAULT_CFG and not (k == "qb_pass" and v == "••••"):
                            ENG.cfg[k] = v
                    if ENG.cfg.get("folder") and not (allowed(ENG.cfg["folder"]) and os.path.isdir(ENG.cfg["folder"])):
                        ENG.cfg["folder"] = ""
                    ENG.save_cfg()
                return self._send(200, {"ok": True})
            if u.path == "/api/trackers/refresh":
                lst = tor.fetch_best_trackers()
                with ENG.lock:
                    ENG.cfg["trackers"] = lst
                    ENG.save_cfg()
                return self._send(200, {"trackers": lst})
            if u.path == "/api/qb/test":
                c = dict(ENG.cfg)
                for k in ("qb_url", "qb_user", "qb_pass"):
                    if b.get(k) and not (k == "qb_pass" and b[k] == "••••"):
                        c[k] = b[k]
                v = QB(c).login()
                return self._send(200, {"ok": True, "version": v})
            if u.path == "/api/history/clear":
                with ENG.lock:
                    ENG.history = []
                    save("historico.json", ENG.history)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "não encontrado"})
        except (ValueError, KeyError, core.SyncError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log_line(traceback.format_exc())
            return self._send(400, {"error": f"{type(e).__name__}: {e}"})


def main():
    global ENG
    ENG = Engine()
    log_line(f"DublaSync {VERSION} iniciado — FFmpeg {ENG.ff_version}, {core.THREADS} threads, pastas {ROOTS}")
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"DublaSync rodando em http://0.0.0.0:{PORT}  (pastas: {', '.join(ROOTS)})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

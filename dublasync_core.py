"""
DublaSync - motor de sincronização.

Pega a faixa de áudio de um arquivo dublado (qualquer formato que o FFmpeg leia),
descobre o atraso, a diferença de velocidade (ex.: PAL 25 fps x 23,976 fps) e
eventuais cortes de cena, e gera um MKV com o vídeo/áudios originais + a dublagem
sincronizada.

Ideia central: dublagens reaproveitam a trilha de música e efeitos do original,
então os "ataques" sonoros (batidas, portas, explosões, música) aparecem nos dois
arquivos nos mesmos instantes, mesmo com idiomas diferentes. Comparamos esses
ataques (envelope de onset) e não a fala.
"""

import json
import re
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
from concurrent.futures import ThreadPoolExecutor

ENV_SR = 16000         # taxa usada só para análise
HOP = 160              # 10 ms por quadro de envelope
FPS = ENV_SR / HOP     # 100 quadros por segundo
WIN = 512
PCM_FMT, PCM_BYTES = "s32le", 4
# segue os timestamps (como o player): tapa/remove buracos acima de 10 ms
ASYNC = "aresample=async=1:min_hard_comp=0.01:first_pts=0"   # 32 bits internos: nada de perda na montagem
THREADS = max(2, min(8, os.cpu_count() or 4))

# Razões de velocidade comuns entre versões (tempo_original / tempo_dublado)
SPEED_CANDIDATES = [
    1.0,
    25 / 23.976, 23.976 / 25,
    25 / 24, 24 / 25,
    24 / 23.976, 23.976 / 24,
    30 / 25, 25 / 30,
]

LANGS = {
    "Português": "por", "Inglês": "eng", "Espanhol": "spa", "Japonês": "jpn",
    "Francês": "fre", "Italiano": "ita", "Alemão": "ger", "Coreano": "kor",
    "Chinês": "chi",
}


class SyncError(Exception):
    pass


# --------------------------------------------------------------------- FFmpeg
def _creationflags():
    return 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW no Windows


def find_tool(name):
    here = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
    exe = name + (".exe" if os.name == "nt" else "")
    for cand in (os.path.join(here, exe), os.path.join(here, "ffmpeg", exe),
                 os.path.join(here, "ffmpeg", "bin", exe)):
        if os.path.isfile(cand):
            return cand
    found = shutil.which(name)
    if found:
        return found
    raise SyncError(
        f"Não encontrei o {name}. Instale o FFmpeg (no Windows: rode instalar.bat "
        f"ou 'winget install Gyan.FFmpeg') ou coloque {exe} na pasta do programa.")


def probe(path):
    cmd = [find_tool("ffprobe"), "-v", "error", "-print_format", "json",
           "-show_streams", "-show_format", path]
    r = subprocess.run(cmd, capture_output=True, creationflags=_creationflags())
    if r.returncode != 0:
        raise SyncError(f"Não consegui ler o arquivo:\n{path}\n{r.stderr.decode(errors='ignore')[-500:]}")
    return json.loads(r.stdout.decode("utf-8", errors="ignore"))


def audio_streams(info):
    return [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]


def pick_dub_stream(info, lang_code):
    """Índice relativo (0:a:N) da faixa dublada: a do idioma pedido, senão a primeira."""
    aud = audio_streams(info)
    if not aud:
        raise SyncError("O arquivo dublado não tem nenhuma faixa de áudio.")
    aliases = {"por": {"por", "pt", "pob", "pt-br", "ptbr", "pt_br"}}.get(lang_code, {lang_code})
    for i, s in enumerate(aud):
        lang = (s.get("tags", {}) or {}).get("language", "").lower()
        if lang in aliases:
            return i
    return 0


def duration_of(info):
    d = info.get("format", {}).get("duration")
    if d:
        return float(d)
    ds = [float(s["duration"]) for s in info.get("streams", []) if s.get("duration")]
    if ds:
        return max(ds)
    raise SyncError("Não consegui descobrir a duração do arquivo original.")


class Reporter:
    """Canal de progresso entre o motor e a interface (ou o terminal)."""

    def __init__(self, on_stage=None, on_log=None, on_info=None):
        self.on_stage = on_stage or self._print_stage
        self.on_log = on_log or print
        self.on_info = on_info or (lambda d: None)
        self.cancel_event = threading.Event()
        self.procs = set()
        self._last = {}

    def _print_stage(self, key, state, detail, frac):
        name = dict(STAGES).get(key, key)
        pct = "" if frac is None else f" {int(frac * 100):3d}%"
        line = f"[{state:>4}] {name}{pct}  {detail or ''}"
        if self._last.get(key) != line and (state != "run" or frac is None or int(frac * 100) % 10 == 0):
            self._last[key] = line
            print(line, flush=True)

    def stage(self, key, state, detail=None, frac=None):
        self.on_stage(key, state, detail, frac)

    def log(self, msg):
        self.on_log(msg)

    def info(self, **kw):
        self.on_info(kw)

    def check(self):
        if self.cancel_event.is_set():
            raise SyncError("Cancelado.")

    def cancel(self):
        self.cancel_event.set()
        for p in list(self.procs):
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass


STAGES = [
    ("ler", "Ler os arquivos"),
    ("orig", "Analisar o áudio original"),
    ("dub", "Analisar o áudio dublado"),
    ("sync", "Encontrar a sincronia"),
    ("ajuste", "Ajustar a dublagem"),
    ("gerar", "Gerar o MKV dual áudio"),
    ("conferir", "Conferir o arquivo final"),
    ("torrent", "Gerar o arquivo .torrent"),
    ("limpar", "Retirar os arquivos antigos"),
]


def _decoder_opts(stream):
    """DTS-HD: para a análise basta o núcleo DTS (bem mais leve que o lossless)."""
    return ["-core_only", "1"] if (stream or {}).get("codec_name") == "dts" else []


def analysis_track(info):
    """Faixa do original mais barata de decodificar (qualquer uma serve: todas
    dividem a mesma música e efeitos). Comentários ficam de fora."""
    cost = {"ac3": 1, "eac3": 1, "aac": 1, "mp3": 1, "opus": 1, "vorbis": 1, "mp2": 1,
            "dts": 2, "flac": 3, "truehd": 4, "mlp": 4}
    best = None
    for i, s in enumerate(audio_streams(info)):
        title = ((s.get("tags", {}) or {}).get("title") or "").lower()
        if "comment" in title or "coment" in title:
            continue
        c = cost.get(s.get("codec_name"), 2)
        if best is None or c < best[0]:
            best = (c, i)
    return best[1] if best else 0


def _stream_start(info, kind="audio", idx=0):
    ss = [s for s in info.get("streams", []) if s.get("codec_type") == kind]
    try:
        return float(ss[idx].get("start_time") or 0)
    except (IndexError, ValueError):
        return 0.0


def decode_mono(path, stream_idx, duration=None, rep=None, key=None, info=None,
                slices=None, extra_out=None):
    """Decodifica em mono 16 kHz para análise. Filmes longos são cortados em fatias
    decodificadas ao mesmo tempo (um processo por núcleo), porque os decodificadores
    de áudio do FFmpeg usam um núcleo só. extra_out: saída adicional (lista de args)
    gravada pelo mesmo processo, para não ler o arquivo duas vezes."""
    rep = rep or Reporter(on_stage=lambda *a: None)
    ffmpeg = find_tool("ffmpeg")
    stream = audio_streams(info)[stream_idx] if info else None
    dopts = _decoder_opts(stream)
    dur = duration or 0
    n = slices if slices is not None else (THREADS if dur > 20 * 60 and not extra_out else 1)
    n = max(1, int(n))
    st0 = _stream_start(info, "audio", stream_idx) if info else 0.0
    L = dur / n if n > 1 else None
    got = [0] * n
    out = [None] * n
    errs = []
    lock = threading.Lock()

    def one(k):
        cmd = [ffmpeg, "-v", "error", "-nostdin"] + dopts
        if n > 1 and k > 0:
            cmd += ["-ss", f"{k * L:.6f}"]  # timestamps absolutos (first_pts=0)
        if n > 1 and k < n - 1:
            cmd += ["-t", f"{L + (0 if k == 0 else 0):.6f}"]
        cmd += ["-i", path, "-vn", "-sn", "-dn", "-map", f"0:a:{stream_idx}",
                "-af", ASYNC, "-ac", "1", "-ar", str(ENV_SR), "-f", "s16le", "pipe:1"]
        if extra_out:
            cmd += ["-map", f"0:a:{stream_idx}"] + extra_out
        err = tempfile.TemporaryFile()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, creationflags=_creationflags())
        with lock:
            rep.procs.add(proc)
        parts = []
        try:
            while True:
                b = proc.stdout.read(1 << 20)
                if not b:
                    break
                parts.append(b)
                got[k] += len(b)
                if rep.cancel_event.is_set():
                    proc.kill()
                    break
            proc.wait()
        finally:
            with lock:
                rep.procs.discard(proc)
        data = b"".join(parts)
        if proc.returncode != 0 and not rep.cancel_event.is_set():
            err.seek(0)
            errs.append(err.read().decode(errors="ignore")[-500:])
        x = np.frombuffer(data, dtype=np.int16)
        if n > 1 and k < n - 1:                      # fatia com tamanho exato
            want = int(round((k + 1) * L * ENV_SR)) - int(round(k * L * ENV_SR))
            x = x[:want] if len(x) >= want else np.concatenate([x, np.zeros(want - len(x), np.int16)])
        out[k] = x

    threads = [threading.Thread(target=one, args=(k,), daemon=True) for k in range(n)]
    for t in threads:
        t.start()
    expected = dur * ENV_SR * 2
    while any(t.is_alive() for t in threads):
        time.sleep(0.2)
        if key and expected:
            g = sum(got)
            extra = f" · {n} núcleos em paralelo" if n > 1 else ""
            rep.stage(key, "run", f"decodificando: {fmt_clock(g / ENV_SR / 2)} de {fmt_clock(dur)}{extra}",
                      min(0.9, 0.9 * g / expected))
    rep.check()
    if errs or any(o is None for o in out):
        raise SyncError(f"Falha ao decodificar o áudio de:\n{path}\n{(errs or [''])[0]}")
    x = np.concatenate(out)
    if len(x) == 0:
        raise SyncError(f"O áudio de {path} veio vazio.")
    return x


def _popen_collect(cmd, rep, chunk_cb=None):
    """Roda o FFmpeg lendo a saída em pedaços (cancelável). Retorna (bytes, erro)."""
    err = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, creationflags=_creationflags())
    rep.procs.add(proc)
    parts = []
    try:
        while True:
            b = proc.stdout.read(1 << 20)
            if not b:
                break
            parts.append(b)
            if chunk_cb:
                chunk_cb(len(b))
            if rep.cancel_event.is_set():
                proc.kill()
                break
        proc.wait()
    finally:
        rep.procs.discard(proc)
    msg = None
    if proc.returncode != 0 and not rep.cancel_event.is_set():
        err.seek(0)
        msg = err.read().decode(errors="ignore")[-500:]
    return b"".join(parts), msg


def decode_region_env(path, stream_idx, start, length, info, rep):
    """Envelope de um trecho do original (busca direta, sem ler o resto do arquivo)."""
    stream = audio_streams(info)[stream_idx] if info else None
    cmd = [find_tool("ffmpeg"), "-v", "error", "-nostdin"] + _decoder_opts(stream)
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-t", f"{length:.3f}", "-i", path, "-vn", "-sn", "-dn", "-map", f"0:a:{stream_idx}",
            "-af", ASYNC, "-ac", "1", "-ar", str(ENV_SR), "-f", "s16le", "pipe:1"]
    data, err = _popen_collect(cmd, rep)
    rep.check()
    if err or len(data) < WIN * 8:
        raise SyncError(f"Falha ao ler um trecho do original ({fmt_clock(start)}).\n{err or ''}")
    return onset_envelope(np.frombuffer(data, dtype=np.int16))


def sample_positions(duration, length=30.0):
    """Onde amostrar: uma a cada ~4 min, de 12 a 40 amostras, cobrindo começo e fim."""
    n = int(min(40, max(12, duration / 240)))
    last = max(0.0, duration - length - 1)
    return [round(last * i / (n - 1), 2) for i in range(n)]


def decode_samples(path, stream_idx, duration, info, rep, key, length=30.0):
    pos = sample_positions(duration, length)
    done = [0]

    def one(p):
        env = decode_region_env(path, stream_idx, p, length, info, rep)
        done[0] += 1
        rep.stage(key, "run", f"amostra {done[0]} de {len(pos)} ({fmt_clock(p)})", 0.9 * done[0] / len(pos))
        return (int(round(p * FPS)), env)

    with ThreadPoolExecutor(THREADS) as pool:
        out = list(pool.map(one, pos))
    return out, len(pos) * length


def _best_lag(ref, seg):
    """Posição em seg onde ref (menor) encaixa melhor (correlação normalizada via FFT)."""
    ref = ref.astype(np.float64) - ref.mean()
    seg = seg.astype(np.float64)
    n = len(seg) + len(ref)
    nfft = 1 << int(math.ceil(math.log2(n)))
    corr = np.fft.irfft(np.fft.rfft(seg, nfft) * np.conj(np.fft.rfft(ref, nfft)), nfft)[: len(seg) - len(ref) + 1]
    c2 = np.concatenate([[0.0], np.cumsum(seg ** 2)])
    c1 = np.concatenate([[0.0], np.cumsum(seg)])
    L = len(ref)
    e = (c2[L:] - c2[:-L]) - (c1[L:] - c1[:-L]) ** 2 / L
    ncc = corr / (np.linalg.norm(ref) * np.sqrt(np.maximum(e, 1e-9)) + 1e-9)
    i = int(np.argmax(ncc))
    return i, float(ncc[i])


def _read_ch0(path, channels, start, count):
    with open(path, "rb") as f:
        f.seek(max(0, start) * channels * PCM_BYTES)
        b = f.read(count * channels * PCM_BYTES)
    x = np.frombuffer(b[: len(b) // (channels * PCM_BYTES) * channels * PCM_BYTES], dtype=np.int32)
    return x.reshape(-1, channels)[:, 0].astype(np.float64)


def decode_dub_parallel(path, stream_idx, duration, info, rep, key, tmp, channels, src_sr, overlap=3.0,
                        slices=None):
    """Decodifica o dublado em fatias simultâneas (um processo por núcleo), na taxa original do
    arquivo e SEM seguir timestamps: arquivos soltos (.ac3, .mp3, AVI antigos) costumam ter
    timestamps imprecisos, e segui-los (ou reamostrar junto com a decodificação) cortaria ou
    deslocaria pedacinhos do áudio. As emendas entre fatias são alinhadas pelo próprio conteúdo
    (cada fatia tem 3 s de sobra de cada lado), com precisão de 1 amostra. O mono de 16 kHz
    da análise é gerado depois, a partir do áudio já decodificado. Retorna (mono, fatias)."""
    ffmpeg = find_tool("ffmpeg")
    n = slices or (THREADS if duration > 10 * 60 else 1)
    L = duration / n
    bps = channels * PCM_BYTES
    paths = [os.path.join(tmp, f"dub_{k:02d}.raw") for k in range(n)]
    errs = []

    def one(k):
        cmd = [ffmpeg, "-v", "error", "-nostdin", "-y"]
        if k > 0:
            cmd += ["-ss", f"{k * L - overlap:.6f}"]
        if k < n - 1:
            cmd += ["-t", f"{L + (overlap if k > 0 else 0) + overlap:.6f}"]
        cmd += ["-i", path, "-vn", "-sn", "-dn", "-map", f"0:a:{stream_idx}",
                "-ac", str(channels), "-ar", str(src_sr), "-f", PCM_FMT, paths[k]]
        try:
            _run(cmd, rep, os.path.join(tmp, "ffmpeg.log"))
        except SyncError as e:
            if not rep.cancel_event.is_set():
                errs.append(str(e))

    threads = [threading.Thread(target=one, args=(k,), daemon=True) for k in range(n)]
    for t in threads:
        t.start()
    expected = duration * src_sr * bps
    while any(t.is_alive() for t in threads):
        time.sleep(0.2)
        g = sum(os.path.getsize(p) for p in paths if os.path.exists(p))
        extra = f" · {n} núcleos em paralelo" if n > 1 else ""
        rep.stage(key, "run", f"decodificando: {fmt_clock(g / bps / src_sr)} de {fmt_clock(duration)}{extra}",
                  min(0.75, 0.75 * g / max(expected, 1)))
    rep.check()
    if errs:
        raise SyncError("Falha ao decodificar o áudio dublado:\n" + errs[0])

    # emendas pelo conteúdo, em amostras da taxa original (canal 1)
    core = [int(round((k + 1) * L * src_sr)) - int(round(k * L * src_sr)) for k in range(n)]
    pre = [0] * n
    w = int(0.25 * src_sr)
    span = int((overlap - 0.5) * src_sr)
    nominal = int(round(overlap * src_sr))
    for k in range(1, n):
        b = pre[k - 1] + core[k - 1]
        ref = _read_ch0(paths[k - 1], channels, b - w, 2 * w)
        lo = max(0, nominal - w - span)
        seg = _read_ch0(paths[k], channels, lo, 2 * w + 2 * span)
        if len(ref) < 2 * w or len(seg) < 2 * w + 1:
            ok = False
        elif not np.any(ref):
            pre[k], ok = nominal, True        # silêncio na emenda: posição nominal serve
        else:
            q, score = _best_lag(ref, seg)
            pre[k], ok = lo + q + w, score > 0.98
        if not ok:
            for pth in paths:
                try:
                    os.remove(pth)
                except OSError:
                    pass
            rep.stage(key, "run", "emendas incertas — decodificando num processo só…", 0.0)
            return decode_dub_parallel(path, stream_idx, duration, info, rep, key, tmp, channels, src_sr,
                                       overlap, slices=1)
    pieces = [{"path": paths[k], "pre": pre[k], "start": int(round(k * L * src_sr)),
               "end": int(round((k + 1) * L * src_sr)) if k < n - 1 else None} for k in range(n)]

    # mono 16 kHz da análise, a partir do áudio decodificado (sem timestamps envolvidos)
    def mono_of(pc):
        cmd = [ffmpeg, "-v", "error", "-nostdin", "-f", PCM_FMT, "-ar", str(src_sr), "-ac", str(channels),
               "-i", pc["path"], "-ac", "1", "-ar", str(ENV_SR), "-f", "s16le", "pipe:1"]
        data, err = _popen_collect(cmd, rep)
        if err:
            raise SyncError("Falha ao preparar o áudio dublado para análise:\n" + err)
        x = np.frombuffer(data, dtype=np.int16)
        p0 = int(round(pc["pre"] * ENV_SR / src_sr))
        if pc["end"] is None:
            return x[p0:]
        cn = int(round(pc["end"] * ENV_SR / src_sr)) - int(round(pc["start"] * ENV_SR / src_sr))
        x = x[p0:p0 + cn]
        return x if len(x) == cn else np.concatenate([x, np.zeros(cn - len(x), np.int16)])

    rep.stage(key, "run", "preparando a análise…", 0.8)
    with ThreadPoolExecutor(THREADS) as pool:
        monos = list(pool.map(mono_of, pieces))
    rep.check()
    return np.concatenate(monos), pieces


def stretch_parallel(pieces, filt, a, src_sr, sr, channels, out_path, rep, log_path, key):
    """Aplica o ajuste de velocidade em cada fatia ao mesmo tempo e emenda tudo,
    cortando a sobra de 1 s de cada emenda."""
    ffmpeg = find_tool("ffmpeg")
    bps = channels * PCM_BYTES
    n = len(pieces)
    done = [0]
    factor = a * sr / src_sr

    def one(pc):
        if not filt:
            return pc["path"]
        outp = pc["path"][:-4] + "_aj.raw"
        _run([ffmpeg, "-v", "error", "-nostdin", "-y", "-f", PCM_FMT, "-ar", str(src_sr), "-ac", str(channels),
              "-i", pc["path"], "-af", ",".join(filt), "-ac", str(channels), "-ar", str(sr),
              "-f", PCM_FMT, outp], rep, log_path)
        os.remove(pc["path"])
        done[0] += 1
        rep.stage(key, "run", f"ajustando velocidade: fatia {done[0]} de {n}"
                              + (f" · {n} núcleos em paralelo" if n > 1 else ""), 0.85 * done[0] / n)
        return outp

    with ThreadPoolExecutor(THREADS) as pool:
        outs = list(pool.map(one, pieces))
    with open(out_path, "wb") as fo:
        for pc, op in zip(pieces, outs):
            rep.check()
            pre = int(round(pc["pre"] * factor))
            if pc["end"] is not None:
                keep = int(round(pc["end"] * factor)) - int(round(pc["start"] * factor))
            else:
                keep = None
            with open(op, "rb") as fi:
                fi.seek(pre * bps)
                left = None if keep is None else keep * bps
                while True:
                    want = 8 << 20 if left is None else min(8 << 20, left)
                    if want == 0:
                        break
                    b = fi.read(want)
                    if not b:
                        break
                    fo.write(b)
                    if left is not None:
                        left -= len(b)
                if left:
                    fo.write(b"\x00" * left)
            os.remove(op)


def fmt_clock(s):
    s = int(max(0, s or 0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ------------------------------------------------------------------- Análise
def _band_matrix(n_bins=WIN // 2 + 1, n_bands=28):
    edges = np.unique(np.geomspace(1, n_bins - 1, n_bands + 1).astype(int))
    m = np.zeros((n_bins, len(edges) - 1), dtype=np.float32)
    centers = []
    for b in range(len(edges) - 1):
        m[edges[b]:edges[b + 1] + 1, b] = 1.0 / (edges[b + 1] - edges[b] + 1)
        centers.append((edges[b] + edges[b + 1]) / 2 * ENV_SR / WIN)
    return m, np.array(centers)


def _band_weights(centers):
    """Peso de cada banda: a faixa da voz humana conta pouco, porque cada versão
    fala um idioma diferente. Grave (explosões, trilha, impactos) e agudo
    (pratos, vidro, passos, chiados) é o que as duas versões compartilham."""
    w = np.ones_like(centers)
    w[(centers >= 150) & (centers < 300)] = 0.5
    w[(centers >= 300) & (centers < 3400)] = 0.2
    return w.astype(np.float32)


def onset_envelope(x):
    """Envelope de 'ataques' (spectral flux em bandas log), 100 quadros/s, normalizado."""
    if len(x) < WIN * 4:
        raise SyncError("Áudio curto demais para sincronizar.")
    x = x.astype(np.float32) / 32768.0
    n_frames = 1 + (len(x) - WIN) // HOP
    window = np.hanning(WIN).astype(np.float32)
    bands, centers = _band_matrix()
    weights = _band_weights(centers)
    out = np.zeros(n_frames, dtype=np.float32)
    prev = None
    block = 40000
    for f0 in range(0, n_frames, block):
        f1 = min(n_frames, f0 + block)
        seg = x[f0 * HOP:(f1 - 1) * HOP + WIN]
        frames = np.lib.stride_tricks.sliding_window_view(seg, WIN)[::HOP][: f1 - f0]
        spec = np.abs(np.fft.rfft(frames * window, axis=1)).astype(np.float32)
        logb = np.log(spec @ bands + 1e-4)
        if prev is None:
            prev = logb[:1]
        d = np.diff(np.vstack([prev, logb]), axis=0)
        out[f0:f1] = np.maximum(d, 0) @ weights
        prev = logb[-1:]
    # remove tendência local (1 s) e normaliza
    k = int(FPS)
    c = np.cumsum(np.concatenate([[0.0], out.astype(np.float64)]))
    idx = np.arange(len(out))
    lo = np.clip(idx - k // 2, 0, len(out))
    hi = np.clip(idx + k // 2 + 1, 0, len(out))
    local = (c[hi] - c[lo]) / (hi - lo)
    e = out - local
    sd = e.std() + 1e-9
    return np.clip(e / sd, -3, 8).astype(np.float32)


class Matcher:
    """Correlação cruzada normalizada de um trecho contra o envelope de referência."""

    def __init__(self, ref):
        self.ref = ref.astype(np.float64)
        self.c2 = np.concatenate([[0.0], np.cumsum(self.ref ** 2)])
        self._full = {}

    def prepare_full(self, L):
        n = len(self.ref) + L
        nfft = 1 << int(math.ceil(math.log2(n)))
        self._full[(0, len(self.ref) - L, nfft)] = np.fft.rfft(self.ref, nfft)

    def search(self, chunk, lo=None, hi=None):
        ref, L = self.ref, len(chunk)
        lo = 0 if lo is None else max(0, int(lo))
        hi = len(ref) - L if hi is None else min(len(ref) - L, int(hi))
        if hi - lo < 10:
            return None
        cn = np.linalg.norm(chunk)
        if cn < 1e-6:
            return None
        n = (hi - lo) + 2 * L
        nfft = 1 << int(math.ceil(math.log2(n)))
        key = (lo, hi, nfft)
        if key in self._full:
            R = self._full[key]
        else:
            R = np.fft.rfft(ref[lo:hi + L], nfft)
        corr = np.fft.irfft(R * np.conj(np.fft.rfft(chunk, nfft)), nfft)[: hi - lo + 1]
        energy = self.c2[lo + L: hi + L + 1] - self.c2[lo: hi + 1]
        ncc = corr / (cn * np.sqrt(np.maximum(energy, 1e-9)))
        i = int(np.argmax(ncc))
        peak = float(ncc[i])
        frac = 0.0
        if 0 < i < len(ncc) - 1:
            a, b, c = ncc[i - 1], ncc[i], ncc[i + 1]
            den = a - 2 * b + c
            if den < 0:
                frac = float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
        masked = ncc.copy()
        masked[max(0, i - 50): i + 51] = -1
        second = float(masked.max()) if len(masked) > 101 else 0.0
        return lo + i + frac, peak, peak / max(second, 1e-3)


def stretch(env, a):
    """Reamostra o envelope dublado para a escala de tempo do original (t = a*d)."""
    if abs(a - 1) < 1e-9:
        return env
    n_out = int(len(env) * a)
    return np.interp(np.arange(n_out) / a, np.arange(len(env)), env).astype(np.float32)


def _cluster_score(offsets, weights, tol=0.15):
    offsets, weights = np.asarray(offsets), np.asarray(weights)
    best, best_o = 0.0, 0.0
    for o in offsets:
        m = np.abs(offsets - o) < tol
        s = weights[m].sum()
        if s > best:
            best, best_o = s, float(np.median(offsets[m]))
    return best, best_o


def analyze(dub_env, orig_env, log=print, prog=lambda f: None, check=lambda: None):
    """Retorna (a, segmentos) onde cada segmento = (d_ini, d_fim, offset) em segundos:
    o trecho dublado [d_ini, d_fim) vai para o tempo original t = a*d + offset."""
    matcher = Matcher(orig_env)
    dub_len = len(dub_env) / FPS

    # 1) escolher a velocidade testando razões comuns
    L = int(20 * FPS)
    n_probe = 40
    starts = np.linspace(0.03 * len(dub_env), 0.95 * len(dub_env) - L, n_probe).astype(int)
    starts = starts[starts >= 0]
    matcher.prepare_full(L)

    def try_speed(a):
        st = stretch(dub_env, a)
        offs, ws = [], []
        for d0 in starts:
            s0 = int(d0 * a)
            if s0 + L > len(st):
                continue
            r = matcher.search(st[s0:s0 + L])
            if r and r[1] > 0.1:
                offs.append((r[0] - s0) / FPS)
                ws.append(r[1])
        if not offs:
            return None
        score, o = _cluster_score(offs, ws)
        return score, a, o

    results = []
    with ThreadPoolExecutor(THREADS) as pool:
        for i, r in enumerate(pool.map(try_speed, SPEED_CANDIDATES)):
            check()
            prog(0.3 * (i + 1) / len(SPEED_CANDIDATES))
            if r:
                results.append(r)
    best = max(results, key=lambda r: r[0]) if results else None
    if best is None or best[0] < 1.0:
        raise SyncError("Não encontrei correspondência entre os áudios. "
                        "Os arquivos são mesmo do mesmo filme?")
    _, a0, o_main = best
    log(f"Velocidade detectada: {a0:.5f}  (atraso inicial aprox. {o_main:+.2f} s)")

    # 2) varredura densa
    st = stretch(dub_env, a0)
    L = int(10 * FPS)
    step = int(5 * FPS)
    W = int(900 * FPS)
    def probe_at(s0):
        exp = s0 + o_main * FPS
        r = matcher.search(st[s0:s0 + L], exp - W, exp + W)
        if not r:
            return None
        return ((s0 + L / 2) / a0 / FPS, (r[0] + L / 2) / FPS, r[1], r[2])

    positions = range(0, len(st) - L, step)
    pts = []
    with ThreadPoolExecutor(THREADS) as pool:
        for i, p in enumerate(pool.map(probe_at, positions)):
            if i % 20 == 0:
                check()
                prog(0.3 + 0.65 * i / max(1, len(positions)))
            if p:
                pts.append(p)
    return _resolve(pts, a0, dub_env, orig_env, log)


def _good(pts):
    return sorted((p for p in pts if p[2] >= 0.2 and p[3] >= 1.25), key=lambda p: p[0])


def _runs(good, a0, tol=0.12):
    """Agrupa os pontos em trechos com o mesmo atraso (cortes de cena mudam o atraso)."""
    runs = []
    for p in good:
        o = p[1] - a0 * p[0]
        if runs and abs(o - np.median([q[1] - a0 * q[0] for q in runs[-1][-5:]])) < tol:
            runs[-1].append(p)
        else:
            runs.append([p])
    runs = [r for r in runs if len(r) >= 3] or [max(runs, key=len)]
    merged = [runs[0]]
    for r in runs[1:]:
        oa = np.median([q[1] - a0 * q[0] for q in merged[-1]])
        ob = np.median([q[1] - a0 * q[0] for q in r])
        if abs(oa - ob) < tol:
            merged[-1].extend(r)
        else:
            merged.append(r)
    return merged


def _resolve(pts, a0, dub_env, orig_env, log):
    """Passos 3–5: trechos, velocidade fina e ponto exato de cada corte."""
    dub_len = len(dub_env) / FPS
    good = _good(pts)
    if len(good) < 5:
        raise SyncError("Correspondência fraca demais para sincronizar com segurança.")
    log(f"Pontos de referência confiáveis: {len(good)} de {len(pts)}")
    runs = _runs(good, a0)

    # 4) refinar a velocidade (inclinação comum a todos os trechos)
    num = den = 0.0
    for r in runs:
        if len(r) < 2:
            continue
        d = np.array([q[0] for q in r]); t = np.array([q[1] for q in r])
        num += ((d - d.mean()) * (t - t.mean())).sum()
        den += ((d - d.mean()) ** 2).sum()
    a = num / den if den > 0 else a0
    if abs(a / a0 - 1) > 0.002:
        a = a0
    offsets = []
    for r in runs:
        res = np.array([q[1] - a * q[0] for q in r])
        med = np.median(res)
        res = res[np.abs(res - med) < 0.08]
        offsets.append(float(res.mean()) if len(res) else float(med))
    all_res = np.concatenate([np.array([q[1] - a * q[0] for q in r]) - o for r, o in zip(runs, offsets)])
    log(f"Velocidade refinada: {a:.6f} | precisão estimada: ±{1000 * np.median(np.abs(all_res)):.0f} ms")

    # 5) achar o ponto exato de cada corte
    ext = np.concatenate([orig_env, np.zeros(int(60 * FPS), np.float32)])
    d_frames = np.arange(len(dub_env)) / FPS

    def contrib(o, i0, i1):
        t = (a * d_frames[i0:i1] + o) * FPS
        y = np.interp(t, np.arange(len(ext)), ext, left=0, right=0)
        return dub_env[i0:i1] * y

    segs = []
    d_start = 0.0
    for k in range(len(runs) - 1):
        oA, oB = offsets[k], offsets[k + 1]
        i0 = int(runs[k][-1][0] * FPS)
        i1 = int(runs[k + 1][0][0] * FPS)
        delta = max(0, int(round((oA - oB) / a * FPS)))  # dublagem tem trecho extra
        i1 = max(i1, i0 + delta + 1)
        cA = np.concatenate([[0], np.cumsum(contrib(oA, i0, i1))])
        cB = np.concatenate([[0], np.cumsum(contrib(oB, i0, i1))])
        n = i1 - i0
        best_s, best_b = -1e18, 0
        for b in range(0, n - delta + 1):
            s = cA[b] + (cB[n] - cB[b + delta])
            if s > best_s:
                best_s, best_b = s, b
        bA = (i0 + best_b) / FPS
        bB = (i0 + best_b + delta) / FPS
        segs.append((d_start, bA, oA))
        d_start = bB
        kind = "cena a mais no original" if oB > oA else "cena a mais na dublagem"
        log(f"Corte em {fmt_time(a * bA + oA)} ({kind}, {abs(oB - oA):.2f} s)")
    segs.append((d_start, dub_len, offsets[-1]))
    return a, segs




def analyze_sampled(dub_env, orig_len, samples, fill, log=print, prog=lambda f: None, check=lambda: None):
    """Sincronia usando só amostras do original (o dublado está inteiro).
    samples: [(quadro_inicial, envelope)]. fill(t0, t1) devolve o envelope de um trecho do
    original; é chamado só onde as amostras indicam um corte de cena."""
    sparse = np.zeros(orig_len, np.float32)
    for t0, seg in samples:
        n = max(0, min(len(seg), orig_len - t0))
        sparse[t0:t0 + n] = seg[:n]

    # 1) velocidade: cada amostra do original é procurada no dublado inteiro
    L = int(20 * FPS)
    probes = []
    for t0, seg in samples:
        if len(seg) >= L:
            k = (len(seg) - L) // 2
            probes.append((t0 + k, seg[k:k + L]))
    if len(probes) < 4:
        raise SyncError("Amostras insuficientes.")

    def try_speed(a):
        st = stretch(dub_env, a)
        if len(st) < L * 2:
            return None
        m = Matcher(st)
        m.prepare_full(L)
        offs, ws = [], []
        for t0, ch in probes:
            r = m.search(ch)
            if r and r[1] > 0.1:
                offs.append((t0 - r[0]) / FPS)
                ws.append(r[1])
        if not offs:
            return None
        score, o = _cluster_score(offs, ws)
        return score, a, o

    results = []
    with ThreadPoolExecutor(THREADS) as pool:
        for i, r in enumerate(pool.map(try_speed, SPEED_CANDIDATES)):
            check()
            prog(0.3 * (i + 1) / len(SPEED_CANDIDATES))
            if r:
                results.append(r)
    best = max(results, key=lambda r: r[0]) if results else None
    if best is None or best[0] < 1.0:
        raise SyncError("Não encontrei correspondência entre os áudios. "
                        "Os arquivos são mesmo do mesmo filme?")
    _, a0, o_main = best
    log(f"Velocidade detectada: {a0:.5f}  (atraso inicial aprox. {o_main:+.2f} s)")

    # 2) pontos de referência dentro de cada amostra
    st = stretch(dub_env, a0)
    m = Matcher(st)
    Lc, step, W = int(10 * FPS), int(5 * FPS), int(900 * FPS)

    def points_from(item):
        t0, seg = item
        out = []
        for k in range(0, len(seg) - Lc + 1, step):
            t = t0 + k
            exp = t - o_main * FPS
            r = m.search(seg[k:k + Lc], exp - W, exp + W)
            if r:
                out.append(((r[0] + Lc / 2) / a0 / FPS, (t + Lc / 2) / FPS, r[1], r[2]))
        return out

    pts = []
    with ThreadPoolExecutor(THREADS) as pool:
        for i, ps in enumerate(pool.map(points_from, samples)):
            check()
            prog(0.3 + 0.4 * (i + 1) / len(samples))
            pts += ps
    good = _good(pts)
    if len(good) < 5:
        raise SyncError("Correspondência fraca nas amostras.")

    # 3) amostras vizinhas discordando = corte de cena entre elas: lê esse trecho inteiro
    runs = _runs(good, a0)
    if len(runs) > 1:
        regions = []
        for k in range(len(runs) - 1):
            tA, tB = runs[k][-1][1], runs[k + 1][0][1]
            oA = np.median([q[1] - a0 * q[0] for q in runs[k]])
            oB = np.median([q[1] - a0 * q[0] for q in runs[k + 1]])
            dlt = abs(oB - oA)
            regions.append((max(0.0, min(tA, tB) - 20 - dlt), min(orig_len / FPS, max(tA, tB) + 20 + dlt)))
        regions.sort()
        merged = [list(regions[0])]
        for r0, r1 in regions[1:]:
            if r0 <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], r1)
            else:
                merged.append([r0, r1])
        log(f"{len(runs) - 1} possível(is) corte(s) de cena: lendo {len(merged)} trecho(s) do original por inteiro")
        filled = []
        for i, (r0, r1) in enumerate(merged):
            check()
            env = fill(r0, r1)
            f0 = int(round(r0 * FPS))
            n = max(0, min(len(env), orig_len - f0))
            sparse[f0:f0 + n] = env[:n]
            filled.append((f0, env[:n]))
            prog(0.7 + 0.25 * (i + 1) / len(merged))
        with ThreadPoolExecutor(THREADS) as pool:
            for ps in pool.map(points_from, filled):
                pts += ps
    prog(0.97)
    return _resolve(pts, a0, dub_env, sparse, log)


def fmt_time(s):
    s = max(0, s)
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"


# ---------------------------------------------------------------- Exportação
def _run(cmd, rep, log_path, key=None, total_sec=None, f0=0.0, f1=1.0, label=""):
    """Roda o FFmpeg mostrando o progresso (lido de -progress)."""
    cmd = [cmd[0], "-progress", "pipe:1", "-nostats"] + cmd[1:]
    with open(log_path, "ab") as lf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=lf,
                                creationflags=_creationflags())
    rep.procs.add(proc)
    try:
        for raw in proc.stdout:
            line = raw.decode(errors="ignore").strip()
            if line.startswith("out_time_us=") and key and total_sec:
                try:
                    t = int(line.split("=")[1]) / 1e6
                except ValueError:
                    continue
                frac = min(1.0, max(0.0, t / total_sec))
                rep.stage(key, "run", f"{label}{fmt_clock(t)} de {fmt_clock(total_sec)}",
                          f0 + (f1 - f0) * frac)
            rep.check()
        proc.wait()
    finally:
        rep.procs.discard(proc)
    rep.check()
    if proc.returncode != 0:
        with open(log_path, "rb") as lf:
            tail = lf.read()[-800:].decode(errors="ignore")
        raise SyncError("O FFmpeg falhou:\n" + tail)


_SOXR = None


def has_soxr():
    global _SOXR
    if _SOXR is None:
        try:
            r = subprocess.run([find_tool("ffmpeg"), "-hide_banner", "-buildconf"],
                               capture_output=True, creationflags=_creationflags())
            _SOXR = b"libsoxr" in r.stdout
        except Exception:  # noqa: BLE001
            _SOXR = False
    return _SOXR


def _resampler(sr):
    if has_soxr():
        return f"aresample={sr}:resampler=soxr:precision=28"
    return f"aresample={sr}:filter_size=64:phase_shift=12:cutoff=0.97"


QUALITY = {
    "ac3": "Dolby AC3 640k (rápido, toca em qualquer TV)",
    "flac": "FLAC sem perda (arquivo maior)",
    "aac": "AAC 256k+ (mais lento)",
}


def describe_speed(a):
    known = {25 / 23.976: "dublagem PAL 25 fps → original 23,976 fps",
             23.976 / 25: "dublagem 23,976 fps → original PAL 25 fps",
             25 / 24: "dublagem PAL 25 fps → original 24 fps",
             24 / 25: "dublagem 24 fps → original PAL 25 fps",
             24 / 23.976: "dublagem 24 fps → original 23,976 fps",
             23.976 / 24: "dublagem 23,976 fps → original 24 fps"}
    if abs(a - 1) < 2e-4:
        return "mesma velocidade"
    for k, v in known.items():
        if abs(a / k - 1) < 3e-4:
            return v
    return f"velocidade ajustada x{a:.5f}"


def describe_file(info):
    """Resumo curto para mostrar na interface."""
    fmt = (info.get("format", {}).get("format_name") or "").split(",")[0].upper()
    ext = os.path.splitext(info.get("format", {}).get("filename", ""))[1].lstrip(".").upper()
    parts = [ext or fmt]
    v = [s for s in info.get("streams", []) if s.get("codec_type") == "video"
         and not (s.get("disposition", {}) or {}).get("attached_pic")]
    if v:
        parts.append(f"{v[0].get('width')}×{v[0].get('height')}")
    try:
        parts.append(fmt_clock(duration_of(info)))
    except SyncError:
        pass
    aud = []
    for s in audio_streams(info):
        lang = (s.get("tags", {}) or {}).get("language", "")
        ch = s.get("channels") or 0
        chs = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(ch, f"{ch}ch")
        aud.append(" ".join(x for x in [lang, (s.get("codec_name") or "").upper(), chs] if x))
    if aud:
        parts.append("áudio: " + ", ".join(aud[:4]) + (" …" if len(aud) > 4 else ""))
    return " · ".join(parts)


def video_height(info):
    v = [s for s in info.get("streams", []) if s.get("codec_type") == "video"
         and not (s.get("disposition", {}) or {}).get("attached_pic")]
    return int(v[0].get("height") or 0) if v else 0


def discard_inputs(paths):
    """Manda para a Lixeira (recuperável). Sem send2trash, move para uma pasta '_antigos'."""
    done = []
    try:
        from send2trash import send2trash
    except ImportError:
        send2trash = None
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        if send2trash:
            send2trash(os.path.abspath(p))
            done.append(("Lixeira", p))
        else:
            dest_dir = os.path.join(os.path.dirname(os.path.abspath(p)), "_antigos (DublaSync)")
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, os.path.basename(p))
            base, ext = os.path.splitext(dest)
            n = 1
            while os.path.exists(dest):
                dest = f"{base} ({n}){ext}"; n += 1
            shutil.move(p, dest)
            done.append((dest_dir, p))
    return done


def build_output(orig, dub, out, lang="por", title=None, dub_default=True, codec="ac3",
                 remove_old=False, rep=None, torrent_opts=None, fast=True):
    """Faz tudo. Retorna um dicionário com o resumo.
    torrent_opts: None (não gera) ou {"trackers": [...], "private": bool, "source": str}."""
    tmp = tempfile.mkdtemp(prefix="dublasync_")
    try:
        return _build(orig, dub, out, lang, title, dub_default, codec, remove_old, rep, tmp, torrent_opts, fast)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _build(orig, dub, out, lang, title, dub_default, codec, remove_old, rep, tmp, torrent_opts=None, fast=True):
    rep = rep or Reporter()
    t_start = time.time()
    ffmpeg = find_tool("ffmpeg")

    # 1) ler
    rep.stage("ler", "run", "lendo informações dos arquivos…")
    oinfo, dinfo = probe(orig), probe(dub)
    if not audio_streams(oinfo):
        raise SyncError("O arquivo original não tem faixa de áudio para servir de referência.")
    dub_idx = pick_dub_stream(dinfo, lang)
    dstream = audio_streams(dinfo)[dub_idx]
    orig_dur = duration_of(oinfo)
    try:
        dub_dur = duration_of(dinfo)
    except SyncError:
        dub_dur = orig_dur
    channels = min(int(dstream.get("channels") or 2), 8)
    sr = int(dstream.get("sample_rate") or 48000)
    sr = 48000 if sr > 48000 else sr
    rep.stage("ler", "ok", f"original {fmt_clock(orig_dur)} · dublado {fmt_clock(dub_dur)} "
                           f"(faixa {dub_idx + 1}: {(dstream.get('codec_name') or '').upper()} "
                           f"{channels}ch)", 1.0)

    # 2+3) analisar os dois em paralelo
    ffl = os.path.join(tmp, "ffmpeg.log")
    src_sr = int(dstream.get("sample_rate") or sr)
    o_idx = analysis_track(oinfo)
    o_note = f" · faixa {o_idx + 1} ({(audio_streams(oinfo)[o_idx].get('codec_name') or '').upper()}, a mais leve)"
    use_samples = fast and orig_dur > 8 * 60     # em vídeos curtos a amostragem não compensa

    def orig_full(key="orig"):
        rep.stage(key, "run", "começando…", 0.0)
        x = decode_mono(orig, o_idx, orig_dur, rep, key, info=oinfo)
        rep.stage(key, "run", "procurando ataques de música e efeitos…", 0.92)
        return onset_envelope(x), len(x) / ENV_SR

    def orig_job():
        if use_samples:
            rep.stage("orig", "run", "lendo amostras espalhadas pelo filme…", 0.0)
            samples, secs = decode_samples(orig, o_idx, orig_dur, oinfo, rep, "orig")
            rep.stage("orig", "ok", f"{len(samples)} amostras = {fmt_clock(secs)} de {fmt_clock(orig_dur)} "
                                    f"({100 * secs / max(orig_dur, 1):.0f}% do filme){o_note}", 1.0)
            return samples
        env, secs = orig_full()
        rep.stage("orig", "ok", f"{fmt_clock(secs)} de áudio analisados{o_note}", 1.0)
        return env

    def dub_job():
        rep.stage("dub", "run", "começando…", 0.0)
        x, pcs = decode_dub_parallel(dub, dub_idx, dub_dur, dinfo, rep, "dub", tmp, channels, src_sr)
        rep.stage("dub", "run", "procurando ataques de música e efeitos…", 0.92)
        env = onset_envelope(x)
        rep.stage("dub", "ok", f"{fmt_clock(len(x) / ENV_SR)} de áudio analisados"
                               + (f" · {len(pcs)} núcleos em paralelo" if len(pcs) > 1 else ""), 1.0)
        return env, pcs

    with ThreadPoolExecutor(2) as pool:
        fo = pool.submit(orig_job)
        fd_ = pool.submit(dub_job)
        try:
            o_res = fo.result()
            dub_env, pieces = fd_.result()
        except BaseException:
            rep.cancel_event.set()
            for p in list(rep.procs):
                p.kill()
            raise

    # 4) sincronia
    rep.stage("sync", "run", "testando velocidades comuns (PAL, cinema)…", 0.0)

    def sprog(f):
        rep.stage("sync", "run", "testando velocidades comuns (PAL, cinema)…" if f < 0.3
                  else "comparando o filme trecho a trecho…", f)

    if use_samples:
        orig_len = int(round(orig_dur * FPS)) + int(FPS)
        try:
            a, segs = analyze_sampled(
                dub_env, orig_len, o_res,
                lambda t0, t1: decode_region_env(orig, o_idx, t0, t1 - t0, oinfo, rep),
                log=rep.log, prog=sprog, check=rep.check)
        except SyncError:
            rep.check()
            # segurança: se as amostras não bastarem, lê o original inteiro e faz do jeito completo
            rep.log("Amostras inconclusivas: analisando o original inteiro")
            orig_env, _ = orig_full("sync")
            rep.stage("orig", "ok", "amostras inconclusivas — original lido inteiro" + o_note, 1.0)
            rep.stage("sync", "run", "comparando o filme trecho a trecho…", 0.3)
            a, segs = analyze(dub_env, orig_env, log=rep.log, prog=sprog, check=rep.check)
    else:
        a, segs = analyze(dub_env, o_res, log=rep.log, prog=sprog, check=rep.check)
    n_cuts = len(segs) - 1
    delay = segs[0][2]
    rep.info(speed=a, speed_text=describe_speed(a), delay=delay, cuts=n_cuts)
    rep.stage("sync", "ok", f"{describe_speed(a)} · atraso {delay:+.2f} s · " +
                            ("sem cortes de cena" if n_cuts == 0 else f"{n_cuts} corte de cena" if n_cuts == 1 else f"{n_cuts} cortes de cena"), 1.0)

    try:
        # 5) ajuste de velocidade + plano de montagem
        rep.stage("ajuste", "run", "corrigindo velocidade e tom da voz…", 0.0)
        stretched = os.path.join(tmp, "dub.raw")
        filt = [_resampler(sr)] if src_sr != sr else []
        if abs(a - 1) > 1e-7:
            r1 = int(round(sr / a))
            tempo = sr / (a * r1)
            filt += [f"asetrate={r1}", _resampler(sr), f"atempo={tempo:.9f}"]
        stretch_parallel(pieces, filt, a, src_sr, sr, channels, stretched, rep, ffl, "ajuste")
        bps = channels * PCM_BYTES
        dub_frames = os.path.getsize(stretched) // bps

        total = int(round(orig_dur * sr))
        plan, cur = [], 0
        for d0, d1, o in segs:
            t0 = int(round((a * d0 + o) * sr))
            t1 = int(round((a * d1 + o) * sr))
            src0 = int(round(a * d0 * sr))
            if t0 < cur:
                src0 += cur - t0
                t0 = cur
            t1 = min(t1, total, t0 + (dub_frames - src0))
            if t1 <= t0:
                continue
            if t0 > cur:
                plan.append((cur, t0, "gap", 0))
            plan.append((t0, t1, "dub", src0))
            cur = t1
        if cur < total:
            plan.append((cur, total, "gap", 0))

        # trechos sem dublagem: busca só esses pedaços do áudio original
        gaps = [(s_, e) for s_, e, k, _ in plan if k == "gap" and (e - s_) > 0.5 * sr]
        gap_sec = sum(e - s_ for s_, e in gaps) / sr
        gap_files = {}
        for gi, (g0, g1) in enumerate(gaps):
            rep.stage("ajuste", "run", f"buscando trecho {gi + 1} de {len(gaps)} do áudio original "
                                       f"({fmt_clock(g0 / sr)})", 0.9 + 0.1 * gi / len(gaps))
            gp = os.path.join(tmp, f"gap{gi}.raw")
            _run([ffmpeg, "-v", "error", "-nostdin", "-y", "-ss", f"{g0 / sr:.6f}",
                  "-t", f"{(g1 - g0) / sr + 0.1:.6f}", "-i", orig, "-vn", "-sn", "-dn",
                  "-map", "0:a:0", "-af", ASYNC + "," + _resampler(sr), "-ac", str(channels),
                  "-ar", str(sr), "-f", PCM_FMT, gp], rep, ffl)
            gap_files[g0] = gp
        rep.stage("ajuste", "ok", "dublagem ajustada" + (
            f" · {fmt_clock(gap_sec)} sem dublagem preenchidos com o áudio original" if gaps else ""), 1.0)

        # 6a) codifica a dublagem num arquivo à parte (pequeno)
        rep.stage("gerar", "run", "codificando a dublagem…", 0.0)
        n_orig_audio = len(audio_streams(oinfo))
        if codec == "ac3" and channels > 6:
            codec = "flac"
        dub_mka = os.path.join(tmp, "dublagem.mka")
        cmd = [ffmpeg, "-v", "error", "-nostdin", "-y", "-f", PCM_FMT, "-ar", str(sr)]
        layout = (dstream.get("channel_layout") or "").replace("(side)", "")
        if layout and channels == int(dstream.get("channels") or 0):
            cmd += ["-ch_layout", layout]
        else:
            cmd += ["-ac", str(channels)]
        cmd += ["-i", "pipe:0"]
        if codec == "ac3":
            cmd += ["-c:a", "ac3", "-b:a", "640k" if channels > 2 else "384k"]
        elif codec == "flac":
            cmd += ["-c:a", "flac", "-compression_level", "8", "-sample_fmt", "s32"]
        else:
            br = "256k" if channels <= 2 else f"{min(768, 112 * channels)}k"
            cmd += ["-c:a", "aac", "-b:a", br, "-aac_coder", "twoloop"]
        cmd += [dub_mka]
        with open(ffl, "ab") as lf:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stderr=lf, creationflags=_creationflags())
        rep.procs.add(proc)
        fd = open(stretched, "rb")
        written, last_ui = 0, 0.0
        chunk = sr * 2
        try:
            for s_, e, kind, src in plan:
                n = e - s_
                pos = 0
                while pos < n:
                    rep.check()
                    m = min(chunk, n - pos)
                    if kind == "dub":
                        fd.seek((src + pos) * bps)
                        buf = fd.read(m * bps)
                    elif s_ in gap_files:
                        with open(gap_files[s_], "rb") as gf:
                            gf.seek(pos * bps)
                            buf = gf.read(m * bps)
                    else:
                        buf = b""
                    if len(buf) < m * bps:
                        buf += b"\x00" * (m * bps - len(buf))
                    proc.stdin.write(buf)
                    pos += m
                    written += m
                    now = time.time()
                    if now - last_ui > 0.25:
                        last_ui = now
                        rep.stage("gerar", "run", f"codificando a dublagem: {fmt_clock(written / sr)} de "
                                                  f"{fmt_clock(orig_dur)}", 0.3 * written / max(total, 1))
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        finally:
            fd.close()
        proc.wait()
        rep.procs.discard(proc)
        rep.check()
        if proc.returncode != 0:
            with open(ffl, "rb") as lf:
                tail = lf.read()[-800:].decode(errors="ignore")
            raise SyncError("O FFmpeg falhou ao codificar a dublagem:\n" + tail)
        os.remove(stretched)

        # 6b) junta tudo (só cópia, sem perda). Ordem: vídeo, áudios originais, dublagem,
        # legendas e, por último, anexos (fontes) — anexos antes de outra entrada
        # disparam um bug do FFmpeg ("Received a packet for an attachment stream").
        def mux_cmd(with_attach, with_subs):
            c = [ffmpeg, "-v", "error", "-nostdin", "-y", "-fflags", "+genpts", "-i", orig, "-i", dub_mka]
            by_type = {"video": [], "audio": [], "subtitle": [], "attachment": []}
            for st_ in oinfo["streams"]:
                t = st_.get("codec_type")
                if t == "video" and (st_.get("disposition", {}) or {}).get("attached_pic"):
                    continue
                if t in by_type:
                    by_type[t].append(st_)
            for st_ in by_type["video"] + by_type["audio"]:
                c += ["-map", f"0:{st_['index']}"]
            c += ["-map", "1:a:0"]
            subs = by_type["subtitle"] if with_subs else []
            for st_ in subs:
                c += ["-map", f"0:{st_['index']}"]
            if with_attach:
                for st_ in by_type["attachment"]:
                    c += ["-map", f"0:{st_['index']}"]
            c += ["-c", "copy"]
            for i, st_ in enumerate(subs):
                if st_.get("codec_name") == "mov_text":
                    c += [f"-c:s:{i}", "srt"]
            k = n_orig_audio
            c += [f"-metadata:s:a:{k}", f"language={lang}", f"-metadata:s:a:{k}", f"title={title or 'Dublado'}"]
            if dub_default:
                for j in range(n_orig_audio):
                    c += [f"-disposition:a:{j}", "0"]
                c += [f"-disposition:a:{k}", "default"]
            c += ["-max_interleave_delta", "0", out]
            return c

        has_att = any(s_.get("codec_type") == "attachment" for s_ in oinfo["streams"])
        has_sub = any(s_.get("codec_type") == "subtitle" for s_ in oinfo["streams"])
        attempts = [(True, True, "")]
        if has_att:
            attempts.append((False, True, " · sem os anexos (fontes) — o FFmpeg recusou copiá-los"))
        if has_sub:
            attempts.append((False, False, " · sem anexos e legendas — o FFmpeg recusou copiá-los"))
        note, last_err = None, None
        for wa, ws, why in attempts:
            try:
                _run(mux_cmd(wa, ws), rep, ffl, "gerar", orig_dur, 0.3, 1.0, "juntando com o vídeo: ")
                note = why
                break
            except SyncError as e:
                if rep.cancel_event.is_set():
                    raise
                last_err = e
                if why:
                    rep.log("Nova tentativa" + why)
                rep.stage("gerar", "run", "o FFmpeg reclamou; tentando de novo de outro jeito…", 0.3)
        if note is None:
            raise SyncError("Não consegui gerar o arquivo final.\n" + str(last_err))
        codec_note = codec.upper() + (note or "")
        rep.info(size=os.path.getsize(out), out=out, codec=codec.upper())
        rep.stage("gerar", "ok", f"{os.path.getsize(out) / 1e9:.2f} GB · dublagem em {codec_note}", 1.0)
    except BaseException:
        if os.path.exists(out):
            try:
                os.remove(out)   # não deixa arquivo pela metade
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 7) conferir
    rep.stage("conferir", "run", "checando faixas e duração…", 0.5)
    try:
        oi = probe(out)
        n_aud = len(audio_streams(oi))
        out_dur = duration_of(oi)
    except SyncError:
        n_aud, out_dur = 0, 0
    ok = n_aud == len(audio_streams(oinfo)) + 1 and abs(out_dur - orig_dur) < 3
    if not ok:
        rep.stage("conferir", "err", f"arquivo final estranho ({n_aud} áudios, {fmt_clock(out_dur)})", 1.0)
        raise SyncError("O arquivo final não passou na conferência. Os arquivos antigos NÃO foram mexidos.")
    rep.stage("conferir", "ok", f"{n_aud} faixas de áudio · duração {fmt_clock(out_dur)} igual ao original", 1.0)

    # 8) .torrent
    tor_path = magnet = None
    if torrent_opts:
        import torrent as tor
        rep.stage("torrent", "run", "calculando os pedaços do arquivo…", 0.0)
        size = os.path.getsize(out)

        def tprog(f):
            rep.stage("torrent", "run", f"verificando {f * size / 1e9:.2f} de {size / 1e9:.2f} GB", f)

        tor_path, magnet, ih = tor.make_torrent(
            out, torrent_opts.get("trackers") or [], private=torrent_opts.get("private", False),
            source=torrent_opts.get("source", ""), progress=tprog, check=rep.check, threads=THREADS)
        kind = "privado" if torrent_opts.get("private") else "público"
        rep.info(torrent=tor_path, magnet=magnet)
        rep.stage("torrent", "ok", f"{os.path.basename(tor_path)} · {kind} · "
                                   f"{len(torrent_opts.get('trackers') or [])} trackers", 1.0)
    else:
        rep.stage("torrent", "skip", "desligado nas configurações", None)

    # 9) retirar antigos
    final_out = out
    if remove_old:
        rep.stage("limpar", "run", "movendo para a Lixeira…", 0.3)
        moved = discard_inputs([dub, orig] if os.path.abspath(dub) != os.path.abspath(orig) else [orig])
        where = moved[0][0] if moved else "Lixeira"
        rep.stage("limpar", "ok", f"{len(moved)} arquivo(s) enviados para: {where}", 1.0)
    else:
        rep.stage("limpar", "skip", "desligado — os arquivos antigos continuam onde estavam", None)

    return {"out": final_out, "torrent": tor_path, "magnet": magnet, "speed": a, "speed_text": describe_speed(a), "delay": delay,
            "cuts": n_cuts, "elapsed": time.time() - t_start}


def ffmpeg_version():
    try:
        r = subprocess.run([find_tool("ffmpeg"), "-hide_banner", "-version"], capture_output=True,
                           creationflags=_creationflags(), timeout=10)
        first = r.stdout.decode(errors="ignore").split("\n")[0]
        m = re.search(r"version\s+n?([0-9][^\s-]*)", first)
        return m.group(1) if m else "ok"
    except Exception:  # noqa: BLE001
        return None


def default_output(orig, oinfo=None, lang="por", style="cena"):
    """Caminho do arquivo novo: mesma pasta do original, nome no padrão escolhido."""
    import naming
    folder = os.path.dirname(os.path.abspath(orig))
    try:
        info = oinfo or probe(orig)
        name = naming.build_name(orig, info, lang, style)
    except Exception:  # noqa: BLE001
        name = os.path.splitext(os.path.basename(orig))[0] + ".DUAL.mkv"
    path = os.path.join(folder, name)
    base, ext = os.path.splitext(path)
    k = 2
    while os.path.abspath(path) == os.path.abspath(orig):
        path = f"{base} ({k}){ext}"; k += 1
    return path

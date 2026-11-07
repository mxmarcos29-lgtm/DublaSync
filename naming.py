"""
Nome do arquivo final no padrão de releases.

Duas convenções:
  cena  -> Titulo.Do.Filme.2023.1080p.BluRay.DUAL.DTS-HD.MA.5.1.x264-GRUPO.mkv
           (Título.Ano.Resolução.Fonte.Idioma.Áudio.Vídeo-GRUPO, separado por pontos)
  plex  -> Titulo Do Filme (2023) [Bluray-1080p][DTS-HD MA 5.1][EN+PT][x264]-GRUPO.mkv
           (formato recomendado pelo TRaSH Guides para Radarr/Plex)

O que é técnico (resolução, HDR, codecs, canais, idiomas) vem do arquivo de verdade
(ffprobe); o que só existe no nome (título, ano, fonte, edição, serviço, grupo) vem
do nome original.
"""

import os
import re

# ------------------------------------------------------------------ padrões do nome
SOURCES = [  # (regex, nome cena, nome plex)
    (r"uhd[ .-]?blu[ .-]?ray", "UHD.BluRay", "Bluray"),
    (r"blu[ .-]?ray|bdremux|bd[ .-]?rip|br[ .-]?rip|bdmv|\bbd\b", "BluRay", "Bluray"),
    (r"web[ .-]?dl|webdl", "WEB-DL", "WEBDL"),
    (r"web[ .-]?rip", "WEBRip", "WEBRip"),
    (r"\bweb\b", "WEB", "WEBDL"),
    (r"hdtv|pdtv", "HDTV", "HDTV"),
    (r"dvd[ .-]?rip", "DVDRip", "DVD"),
    (r"\bdvd(?:5|9|r)?\b", "DVD", "DVD"),
    (r"hd[ .-]?rip", "HDRip", "HDRip"),
]
REMUX = r"\bremux\b"
SERVICES = ["AMZN", "NF", "DSNP", "HMAX", "MAX", "ATVP", "PCOK", "PMTP", "HULU", "GLBP",
            "STAN", "iT", "CRAV", "MUBI", "CR", "SHO", "STARZ", "PLAY"]
EDITIONS = [(r"director'?s[ .-]?cut", "Directors.Cut"), (r"extended(?:[ .-]?(?:cut|edition))?", "Extended"),
            (r"\bunrated\b", "Unrated"), (r"\buncut\b", "Uncut"), (r"\bremastered\b", "Remastered"),
            (r"\bimax\b", "IMAX"), (r"theatrical(?:[ .-]?cut)?", "Theatrical"), (r"\bcriterion\b", "Criterion"),
            (r"ultimate[ .-]?(?:cut|edition)", "Ultimate.Edition"), (r"special[ .-]?edition", "Special.Edition")]
FLAGS = [r"\bproper\b", r"\brepack\b"]
# palavras que não são título
JUNK = (r"dublado|dublada|legendado|nacional|dual(?:[ .-]?a[uú]dio)?|multi|pt[ .-]?br|ptbr|portugu[eê]s|"
        r"torrent|www\.[^ ]+|\[[^\]]*\]")
LANG2 = {"por": "PT", "pob": "PT", "eng": "EN", "spa": "ES", "jpn": "JA", "fre": "FR", "fra": "FR",
         "ita": "IT", "ger": "DE", "deu": "DE", "kor": "KO", "chi": "ZH", "zho": "ZH", "rus": "RU"}


def _channels(n):
    return {1: "1.0", 2: "2.0", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}.get(int(n or 2), f"{n}.0")


def audio_tag(s):
    """('DTS-HD MA', '5.1', atmos?) a partir de um stream do ffprobe."""
    c = (s.get("codec_name") or "").lower()
    prof = (s.get("profile") or "").lower()
    atmos = "atmos" in prof
    if c == "dts":
        name = "DTS-HD MA" if "ma" in prof else "DTS-HD HRA" if "hra" in prof or "high res" in prof \
            else "DTS-X" if "dts:x" in prof or "dts-x" in prof else "DTS-ES" if "es" in prof.split() else "DTS"
        if "dts:x" in prof and "ma" in prof:
            name = "DTS-X"
    elif c == "truehd":
        name = "TrueHD"
    elif c == "eac3":
        name = "DDP"
    elif c == "ac3":
        name = "DD"
    elif c == "aac":
        name = "AAC"
    elif c == "flac":
        name = "FLAC"
    elif c == "opus":
        name = "Opus"
    elif c in ("mp3", "mp2"):
        name = c.upper()
    elif c.startswith("pcm"):
        name = "LPCM"
    else:
        name = c.upper() or "AUDIO"
    return name, _channels(s.get("channels")), atmos


def video_info(info):
    v = [s for s in info.get("streams", []) if s.get("codec_type") == "video"
         and not (s.get("disposition", {}) or {}).get("attached_pic")]
    if not v:
        return None
    v = v[0]
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    if w >= 3200 or h >= 1600:
        res = "2160p"
    elif w >= 1800 or h >= 1000:
        res = "1080p"
    elif w >= 1200 or h >= 700:
        res = "720p"
    elif h >= 560:
        res = "576p"
    else:
        res = "480p" if h >= 400 else "SD"
    hdr = []
    side = " ".join(str(x.get("side_data_type", "")) for x in v.get("side_data_list", []) or [])
    if "DOVI" in side.upper() or (v.get("codec_tag_string") or "").lower() in ("dvh1", "dvhe", "dav1"):
        hdr.append("DV")
    tr = v.get("color_transfer") or ""
    if tr == "smpte2084":
        hdr.append("HDR10")
    elif tr == "arib-std-b67":
        hdr.append("HLG")
    bit10 = "10" in (v.get("pix_fmt") or "")
    codec = {"h264": "H.264", "hevc": "H.265", "av1": "AV1", "vc1": "VC-1", "mpeg2video": "MPEG-2",
             "mpeg4": "XviD", "vp9": "VP9"}.get(v.get("codec_name"), (v.get("codec_name") or "").upper())
    return {"res": res, "hdr": hdr, "codec": codec, "bit10": bit10}


def parse_name(path):
    """Extrai do nome: título, ano, SxxEyy, fonte, remux, edição, serviço, flags, grupo, tags de vídeo."""
    base = os.path.splitext(os.path.basename(path))[0]
    low = base.lower()
    r = {"title": None, "year": None, "episode": None, "source": None, "source_plex": None,
         "remux": bool(re.search(REMUX, low)), "edition": [], "service": None, "flags": [],
         "group": None, "vcodec": None, "hdr_name": []}
    m = re.search(r"-([A-Za-z0-9][A-Za-z0-9_.]{1,20})$", base)
    if m and not re.fullmatch(r"(?i)dl|rip|hd|ma|x26[45]|h\.?26[45]|\d+", m.group(1)):
        r["group"] = m.group(1)
        base_ng = base[:m.start()]
    else:
        base_ng = base
    for rx, scene, plex in SOURCES:
        if re.search(rx, low):
            r["source"], r["source_plex"] = scene, plex
            break
    for rx, tag in EDITIONS:
        if re.search(rx, low):
            r["edition"].append(tag)
    for f in FLAGS:
        if re.search(f, low):
            r["flags"].append(re.sub(r"\\b", "", f).upper())
    toks = re.split(r"[ ._\[\]()]+", base_ng)
    for t in toks:
        if t in SERVICES or t.upper() in [x.upper() for x in SERVICES if len(x) > 2]:
            r["service"] = t if t in SERVICES else t.upper()
            break
    vm = re.search(r"(?i)\b(x26[45]|h\.?26[45]|hevc|avc|av1|xvid|divx|vc-?1)\b", base)
    if vm:
        t = vm.group(1).lower()
        r["vcodec"] = {"x264": "x264", "x265": "x265", "h264": "H.264", "h.264": "H.264",
                       "h265": "H.265", "h.265": "H.265", "hevc": "HEVC", "avc": "AVC", "av1": "AV1",
                       "xvid": "XviD", "divx": "DivX", "vc1": "VC-1", "vc-1": "VC-1"}[t]
    if re.search(r"(?i)\bhdr10(\+|plus)", base):
        r["hdr_name"].append("HDR10+")
    if re.search(r"(?i)\b(dv|dovi|dolby[ .]?vision)\b", base):
        r["hdr_name"].append("DV")

    # título = o que vem antes do primeiro marcador técnico (ano, SxxEyy, resolução, fonte…)
    sep = re.sub(r"[._]+", " ", base_ng)
    ep = re.search(r"(?i)\bS(\d{1,2})[ .]?E(\d{1,3})(?:-?E(\d{1,3}))?\b", sep)
    if ep:
        r["episode"] = f"S{int(ep.group(1)):02d}E{int(ep.group(2)):02d}" + (
            f"E{int(ep.group(3)):02d}" if ep.group(3) else "")
    years = [m for m in re.finditer(r"(?<!\d)(19[2-9]\d|20[0-4]\d)(?!\d)", sep)]
    years = [y for y in years if y.start() > 0]           # filme chamado "1917" etc.
    markers = [m.start() for m in [ep] if m]
    if years:
        r["year"] = years[-1].group(1)
        markers.append(years[-1].start())
    tech = re.search(r"(?i)\b(2160p|1080[pi]|720p|576p|480p|4k|uhd|blu[ -]?ray|web[ -]?dl|webrip|web|"
                     r"hdtv|dvdrip|bdrip|brrip|remux|x26[45]|h ?26[45]|hevc|dublado|legendado|dual|"
                     r"nacional|multi|extended|unrated|directors?|imax|proper|repack)\b", sep)
    if tech:
        markers.append(tech.start())
    title = sep[:min(markers)] if markers else sep
    title = re.sub(r"(?i)\b(" + JUNK + r")\b", " ", title)
    title = re.sub(r"[\[\]()-]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -")
    r["title"] = title or os.path.splitext(os.path.basename(path))[0]
    return r


def _main_audio(oinfo):
    aud = [s for s in oinfo.get("streams", []) if s.get("codec_type") == "audio"]
    for s in aud:  # a "melhor" faixa costuma ser a primeira; pula comentário
        t = ((s.get("tags", {}) or {}).get("title") or "").lower()
        if "comment" not in t and "coment" not in t:
            return s
    return aud[0] if aud else None


def _langs(oinfo, dub_lang):
    out = []
    for s in oinfo.get("streams", []):
        if s.get("codec_type") == "audio":
            l2 = LANG2.get(((s.get("tags", {}) or {}).get("language") or "").lower())
            if l2 and l2 not in out:
                out.append(l2)
    d = LANG2.get(dub_lang, dub_lang[:2].upper())
    if d not in out:
        out.append(d)
    return out


def safe(name):
    return re.sub(r'[<>:"/\\|?*]', "", name).strip(" .")


def build_name(orig_path, oinfo, dub_lang="por", style="cena"):
    p = parse_name(orig_path)
    v = video_info(oinfo) or {"res": None, "hdr": [], "codec": None, "bit10": False}
    a = _main_audio(oinfo)
    aname, ach, atmos = audio_tag(a) if a else (None, None, False)
    hdr = list(dict.fromkeys(p["hdr_name"] + v["hdr"]))
    if "HDR10+" in hdr and "HDR10" in hdr:
        hdr.remove("HDR10")
    vcodec = p["vcodec"] or v["codec"]
    if p["remux"] and vcodec in ("H.264", "x264"):
        vcodec = "AVC"
    if p["remux"] and vcodec in ("H.265", "x265"):
        vcodec = "HEVC"

    if style == "plex":
        head = p["title"] + (f" ({p['year']})" if p["year"] else "") + (f" - {p['episode']}" if p["episode"] else "")
        if p["edition"]:
            head += " {edition-" + " ".join(e.replace(".", " ") for e in p["edition"]) + "}"
        q = "-".join(x for x in [(p["source_plex"] or "") + (" Remux" if p["remux"] else ""), v["res"]] if x)
        if p["flags"]:
            q += " " + " ".join(f.title() for f in p["flags"])
        parts = [f"[{q}]" if q else ""]
        if hdr:
            parts.append("[" + " ".join(hdr) + "]")
        if aname:
            parts.append(f"[{aname}{' Atmos' if atmos else ''} {ach}]")
        parts.append("[" + "+".join(_langs(oinfo, dub_lang)) + "]")
        if vcodec:
            parts.append(f"[{vcodec}{' 10bit' if v['bit10'] and vcodec in ('x265', 'HEVC', 'H.265') else ''}]")
        name = head + " " + "".join(parts) + (f"-{p['group']}" if p["group"] else "")
    else:
        t = re.sub(r"\s+", ".", p["title"])
        bits = [t, p["episode"] or p["year"]]
        if p["episode"] and p["year"]:
            bits = [t, p["year"], p["episode"]]
        bits += p["edition"] + [v["res"], p["service"],
                                (p["source"] + ".REMUX") if p["remux"] and p["source"] else p["source"]]
        bits += [".".join(hdr)] if hdr else []
        bits += p["flags"]
        bits += ["DUAL"]
        if aname:
            sep_ch = "" if aname in ("DD", "DDP", "AAC") else "."   # DD5.1, DDP5.1, AAC2.0
            bits.append(aname.replace(" ", ".") + sep_ch + ach + (".Atmos" if atmos else ""))
        bits.append(vcodec.replace(" ", ".") if vcodec else None)
        name = ".".join(b for b in bits if b) + (f"-{p['group']}" if p["group"] else "")
    return safe(name) + ".mkv"


STYLES = {"cena": "Padrão torrent  (Filme.2023.1080p.BluRay.DUAL…)",
          "plex": "Padrão Plex/Radarr  (Filme (2023) [Bluray-1080p]…)"}

"""
DublaSync - junta a dublagem de um arquivo com o vídeo de alta qualidade de outro.

Uso com janela:  dois cliques em "Abrir DublaSync.bat" (ou DublaSync.pyw)
Linha de comando:
    py DublaSync.pyw DUBLADO ORIGINAL [-o SAIDA.mkv] [--codec ac3|flac|aac]
                     [--original-padrao] [--retirar-antigos]
"""

import argparse
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dublasync_core as core  # noqa: E402
import naming  # noqa: E402


# ------------------------------------------------------------------ terminal
def run_cli(argv):
    p = argparse.ArgumentParser(description="Sincroniza a dublagem com o vídeo original e gera um MKV dual áudio.")
    p.add_argument("dublado")
    p.add_argument("original")
    p.add_argument("-o", "--saida")
    p.add_argument("--idioma", default="por")
    p.add_argument("--codec", default="ac3", choices=list(core.QUALITY))
    p.add_argument("--original-padrao", action="store_true")
    p.add_argument("--nome", default="cena", choices=list(naming.STYLES),
                   help="padrão do nome: cena (torrent) ou plex (Plex/Radarr)")
    p.add_argument("--torrent", action="store_true", help="gera também o .torrent (trackers públicos padrão)")
    p.add_argument("--completa", action="store_true", help="lê o original inteiro (sem amostragem)")
    p.add_argument("--retirar-antigos", action="store_true",
                   help="ao terminar, manda o dublado e o original para a Lixeira")
    a = p.parse_args(argv)
    out = a.saida or core.default_output(a.original, lang=a.idioma, style=a.nome)
    print("Arquivo novo:", os.path.basename(out))
    try:
        r = core.build_output(a.original, a.dublado, out, lang=a.idioma, codec=a.codec,
                              dub_default=not a.original_padrao, remove_old=a.retirar_antigos,
                              torrent_opts={"trackers": __import__("torrent").DEFAULT_TRACKERS} if a.torrent else None,
                              fast=not a.completa)
    except core.SyncError as e:
        print("ERRO:", e, file=sys.stderr)
        return 1
    print(f"\nPronto em {int(r['elapsed'])} s: {r['out']}")
    return 0


def _crash(exc_text):
    """Se a janela não conseguir abrir, mostra e salva o erro (o pyw não tem console)."""
    here = os.path.dirname(os.path.abspath(__file__))
    log = os.path.join(here, "DublaSync-erro.txt")
    try:
        with open(log, "w", encoding="utf-8") as f:
            f.write(exc_text)
    except OSError:
        pass
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("DublaSync — erro ao abrir",
                             exc_text[-1500:] + f"\n\n(salvo em {log})")
        r.destroy()
    except Exception:  # noqa: BLE001
        pass


def run_gui():
    import traceback
    try:
        import ui
        ui.run_gui()
    except Exception:  # noqa: BLE001
        _crash(traceback.format_exc())


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(run_cli(sys.argv[1:]))
    run_gui()

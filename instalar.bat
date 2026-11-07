@echo off
chcp 65001 >nul
echo === Instalando o que o DublaSync precisa (so precisa rodar uma vez) ===
echo.
where py >nul 2>nul
if errorlevel 1 (
  echo Instalando Python...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo Instalando FFmpeg...
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
)
echo Instalando bibliotecas (numpy, arrastar-e-soltar, Lixeira)...
py -m pip install --upgrade numpy tkinterdnd2 send2trash
if errorlevel 1 (
  echo.
  echo Se deu erro acima, FECHE esta janela e rode o instalar.bat de novo
  echo (o Windows precisa reabrir para enxergar o Python recem-instalado).
) else (
  echo.
  echo Tudo pronto! Agora e so abrir o "Abrir DublaSync.bat".
)
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === DublaSync - modo diagnostico ===
echo.
echo Python:
py --version
py -c "import sys; print(sys.executable)"
echo.
echo Bibliotecas:
py -c "import numpy; print('numpy', numpy.__version__)"
py -c "import tkinter; print('tkinter', tkinter.TkVersion)"
py -c "import tkinterdnd2; print('tkinterdnd2 ok')"
py -c "import send2trash; print('send2trash ok')"
echo.
echo FFmpeg:
ffmpeg -hide_banner -version | findstr /b "ffmpeg"
echo.
echo Abrindo o programa (erros aparecem aqui embaixo):
py -X faulthandler "%~dp0DublaSync.pyw"
echo.
echo ------------------------------------------------------------
echo Se apareceu algum erro acima, tire um print desta janela.
pause

@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -c "import PIL, PySide6" >nul 2>nul
    if errorlevel 1 (
        echo Pillow ou PySide6 est absent de Python 3.
        echo Installer les dependances avec : py -3 -m pip install -r requirements.txt
        pause
        exit /b 1
    )
    py -3 triphoto_qt.py
) else (
    python -c "import PIL, PySide6" >nul 2>nul
    if errorlevel 1 (
        echo Pillow ou PySide6 est absent de Python.
        echo Installer les dependances avec : python -m pip install -r requirements.txt
        pause
        exit /b 1
    )
    python triphoto_qt.py
)
if %errorlevel% neq 0 (
    echo.
    echo TriPhoto n'a pas pu demarrer.
    echo Python 3, Pillow et PySide6 sont necessaires.
    pause
)
endlocal

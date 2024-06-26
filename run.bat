@echo off
SET VENV_NAME=meshtastic_venv

IF NOT EXIST %VENV_NAME%\Scripts\activate.bat (
    echo Creating virtual environment...
    python -m venv %VENV_NAME%
)

call %VENV_NAME%\Scripts\activate.bat

echo Upgrading pip...
python -m pip install --upgrade pip

echo Installing required packages...
pip install flask flask-socketio flask-cors pyserial meshtastic

echo Starting the Flask application...
python app.py

pause
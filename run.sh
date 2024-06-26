#!/bin/bash

VENV_NAME="meshtastic_venv"

if [ ! -d "$VENV_NAME" ]; then
    echo "Creating virtual environment..."
    python3 -m venv $VENV_NAME
fi

source $VENV_NAME/bin/activate

echo "Upgrading pip..."
pip install --upgrade pip

echo "Installing required packages..."
pip install flask flask-socketio flask-cors pyserial meshtastic

echo "Starting the Flask application..."
python app.py
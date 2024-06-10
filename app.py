from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

import serial
import serial.tools.list_ports

import threading
import logging

from meshtastic import serial_interface

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins='*')

serial_connection = None
current_channel = None

def list_serial_ports():
    try:
        ports = serial.tools.list_ports.comports()
        return [port.device for port in ports]
    except Exception as e:
        logging.error(f'Error detecting COM ports: {str(e)}')
        return []

def open_serial_connection(port):
    global serial_connection
    try:
        if serial_connection:
            serial_connection.close()
            logging.info("Closed existing serial connection.")
        serial_connection = serial_interface.SerialInterface(port)
        logging.info(f"Successfully connected to {port}")
        return True
    except Exception as e:
        logging.error(f"Failed to connect to {port}: {str(e)}")
        return False

def read_from_serial():
    while True:
        if serial_connection:
            try:
                packet = serial_connection.getFromRadio(timeout=1)
                if packet:
                    handle_meshtastic_packet(packet)
            except Exception as e:
                logging.error(f"Error reading from serial: {str(e)}")
                break

def handle_meshtastic_packet(packet):
    try:
        packet_data = str(packet)
        socketio.emit('receive_data', {'data': packet_data})
        logging.debug(f"Received data: {packet_data}")

        if isinstance(packet, serial_interface.Channel):
            channel_index = packet.index
            channel_name = packet.settings.name
            socketio.emit('channel_info', {'index': channel_index, 'name': channel_name})
            logging.info(f'Channel info - Index: {channel_index}, Name: {channel_name}')
        elif isinstance(packet, serial_interface.MeshPacket):
            channel = packet.channel
            message = packet.decoded.payload.decode('utf-8')
            socketio.emit('receive_message', {'channel': channel, 'message': message})
            logging.info(f'Message on {channel}: {message}')
    except Exception as e:
        logging.error(f"Error handling Meshtastic packet: {str(e)}")

def get_channel_list():
    if serial_connection:
        try:
            channels = serial_connection.getChannels()
            return [{'index': channel.index, 'name': channel.settings.name} for channel in channels]
        except Exception as e:
            logging.error(f"Error fetching channels: {str(e)}")
    return []

@app.route('/')
def index():
    ports = list_serial_ports()
    channels = get_channel_list()
    return render_template('index.html', ports=ports, channels=channels, connected=serial_connection is not None)

@socketio.on('connect')
def handle_connect():
    logging.info('Client connected')
    emit('connection_status', {'connected': serial_connection is not None})

@socketio.on('select_port')
def handle_select_port(data):
    if open_serial_connection(data['port']):
        emit('port_selected', {'status': 'connected', 'port': data['port']})
        channels = get_channel_list()
        emit('channel_list', {'channels': channels})
    else:
        emit('error', {'error': 'Failed to connect to port'})

@socketio.on('select_channel')
def handle_select_channel(data):
    global current_channel
    current_channel = data['channel']
    logging.info(f'Channel selected: {current_channel}')

@socketio.on('send_message')
def handle_send_message(data):
    if serial_connection:
        try:
            message = data['message']
            serial_connection.sendText(message, wantAck=True, channelIndex=current_channel)
            emit('message_sent', {'message': message})
            logging.info(f"Message sent on {current_channel}: {message}")
        except Exception as e:
            emit('error', {'error': 'Failed to send message'})

@socketio.on('disconnect')
def handle_disconnect():
    logging.info('Client disconnected')
    if serial_connection:
        serial_connection.close()
        logging.info('Serial connection closed')

if __name__ == '__main__':
    available_ports = list_serial_ports()
    if available_ports:
        logging.info(f"Available ports: {available_ports}")
    else:
        logging.warning("No COM ports detected at startup.")
    threading.Thread(target=read_from_serial, daemon=True).start()
    socketio.run(app, host='127.0.0.1', port=5678)
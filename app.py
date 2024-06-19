from flask import Flask, render_template, request, Response, stream_with_context

import serial
import serial.tools.list_ports

import json
import logging
import queue 

from meshtastic.serial_interface import SerialInterface
from meshtastic import portnums_pb2, mesh_pb2
from pubsub import pub

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.config['serial_interface'] = None
app.config['sse_subscribers'] = []

def list_serial_ports():
    return [port.device for port in serial.tools.list_ports.comports()]

def connect_serial(port):
    try:
        interface = SerialInterface(port)
        interface.sendText("Connected to Meshtastic device")
        pub.subscribe(on_receive, "meshtastic.receive")
        pub.subscribe(on_connection, "meshtastic.connection.established")
        app.config['serial_interface'] = interface
        logging.info(f"Connected to Meshtastic device on {port}")
        return True
    except Exception as e:
        logging.error(f"Error connecting to Meshtastic device: {e}")
        return False

def on_connection(interface, topic=pub.AUTO_TOPIC):
    logging.info("Connected to Meshtastic device")
    interface.sendText("Connected to Meshtastic device")
    info = interface.getMyNodeInfo()
    logging.info(f"Connected to {info}")

    radio_config = mesh_pb2.RadioConfig()
    interface._getRadioConfig(radio_config)
    channels = radio_config.channel_settings

    primary_channel = None
    for channel in channels:
        channel_info = {
            'index': channel.index,
            'name': channel.settings.name
        }
        if channel.role == mesh_pb2.Channel.Role.PRIMARY:
            primary_channel = channel_info
        else:
            send_sse('channel_info', json.dumps(channel_info))

    if primary_channel:
        send_sse('primary_channel', json.dumps(primary_channel))
        
def on_receive(packet, interface):
    logging.debug(f"Received: {packet}")
    decode_packet(packet, interface)

def decode_packet(packet, interface, parent="MainPacket", filler="", filler_char=""):
    if isinstance(packet, dict):
        for key, value in packet.items():
            if isinstance(value, dict):
                decode_packet(value, interface, f"{parent}/{key}", filler, filler_char)
            else:
                logging.debug(f"{filler}{key}: {value}")
                handle_packet_data(key, value, packet, interface)
    else:
        logging.warning("Warning: Not a packet!")

def handle_packet_data(key, value, packet, interface):
    if key == "decoded":
        decoded_data = packet["decoded"]
        portnum = decoded_data.get("portnum")
        
        if portnum == portnums_pb2.PortNum.TEXT_MESSAGE_APP:
            handle_text_message(decoded_data, interface)
        elif portnum == portnums_pb2.PortNum.POSITION_APP:
            handle_position_message(decoded_data, interface)
        elif portnum == portnums_pb2.PortNum.NODEINFO_APP:
            handle_nodeinfo_message(decoded_data, interface)
        elif portnum == portnums_pb2.PortNum.TELEMETRY_APP:
            handle_telemetry_message(decoded_data, interface)

def handle_text_message(decoded_data, interface):
    text = decoded_data.get("text", "")
    sender = decoded_data.get("from", "Unknown")
    logging.info(f"Received text message from {sender}: {text}")
    send_sse('text_message', json.dumps({'sender': sender, 'text': text}))

def handle_position_message(decoded_data, interface):
    position = decoded_data.get("position", {})
    sender = decoded_data.get("from", "Unknown")
    latitude = position.get("latitude", 0.0)
    longitude = position.get("longitude", 0.0)
    altitude = position.get("altitude", 0)
    logging.info(f"Received position from {sender}: Lat: {latitude}, Lon: {longitude}, Alt: {altitude}")
    send_sse('position_data', json.dumps({'sender': sender, 'latitude': latitude, 'longitude': longitude, 'altitude': altitude}))

def handle_nodeinfo_message(decoded_data, interface):
    nodeinfo = decoded_data.get("user", {})
    sender = decoded_data.get("from", "Unknown")
    logging.info(f"Received nodeinfo from {sender}: {nodeinfo}")
    send_sse('user_data', json.dumps({
        'sender': sender,
        'id': nodeinfo.get("id", ""),
        'longName': nodeinfo.get("longName", ""),
        'shortName': nodeinfo.get("shortName", ""),
        'macaddr': nodeinfo.get("macaddr", ""),
        'hwModel': nodeinfo.get("hwModel", "")
    }))

def handle_telemetry_message(decoded_data, interface):
    telemetry = decoded_data.get("telemetry", {})
    sender = decoded_data.get("from", "Unknown")
    logging.info(f"Received telemetry from {sender}: {telemetry}")
    device_metrics = telemetry.get("deviceMetrics", {})
    send_sse('telemetry_data', json.dumps({
        'sender': sender,
        'batteryLevel': device_metrics.get("batteryLevel", 0),
        'voltage': device_metrics.get("voltage", 0.0),
        'channelUtilization': device_metrics.get("channelUtilization", 0.0),
        'airUtilTx': device_metrics.get("airUtilTx", 0.0),
        'uptimeSeconds': device_metrics.get("uptimeSeconds", 0)
    }))

def send_sse(event, data):
    for queue in app.sse_queues:
        queue.put(f"event: {event}\ndata: {data}\n\n")

@app.route('/')
def index():
    ports = list_serial_ports()
    return render_template('index.html', ports=ports)

@app.route('/connect', methods=['POST'])
def connect():
    port = request.form['port']
    if connect_serial(port):
        return {'status': 'connected', 'message': f'Connected to {port}'}
    else:
        return {'status': 'error', 'message': 'Failed to connect'}

@app.route('/disconnect', methods=['POST'])
def disconnect():
    interface = app.config['serial_interface']
    if interface:
        interface.close()
        app.config['serial_interface'] = None
        logging.info("Disconnected from Meshtastic device")
        return {'status': 'disconnected'}
    else:
        return {'status': 'error', 'message': 'Not connected'}

@app.route('/send_message', methods=['POST'])
def send_message():
    message = request.form['message']
    channel_index = int(request.form['channel'])
    interface = app.config['serial_interface']
    if interface:
        interface.sendText(message, channelIndex=channel_index)
        logging.info(f"Sent message: {message} on channel {channel_index}")
        return {'status': 'sent'}
    else:
        return {'status': 'error', 'message': 'Not connected'}

@app.route('/stream')
def stream():
    def event_stream():
        subscriber_queue = app.config['sse_subscribers']
        subscriber_queue_item = queue.Queue()  #
        subscriber_queue.append(subscriber_queue_item)
        try:
            while True:
                data = subscriber_queue_item.get()
                yield f"data: {data}\n\n"
        finally:
            subscriber_queue.remove(subscriber_queue_item)

    return Response(event_stream(), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run()

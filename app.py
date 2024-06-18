from flask import Flask, render_template, request, Response
from flask import stream_with_context

import serial
import serial.tools.list_ports

import json
import threading
import queue as queue_module
import logging

from meshtastic.serial_interface import SerialInterface
from meshtastic import portnums_pb2
from meshtastic import clientonly_pb2
from meshtastic import mesh_pb2

from pubsub import pub

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.sse_queues = []

serial_interface = None
current_channel = None

def list_serial_ports():
    try:
        ports = serial.tools.list_ports.comports()
        return [port.device for port in ports]
    except Exception as e:
        logging.error(f'Error detecting COM ports: {str(e)}')
        return []

def open_serial_connection(port):
    global serial_interface
    try:
        if serial_interface:
            serial_interface.close()
            logging.info("Closed existing serial connection.")
        serial_interface = SerialInterface(port)
        pub.subscribe(on_receive, "meshtastic.receive")
        pub.subscribe(on_connection, "meshtastic.connection.established")
        logging.info(f"Successfully connected to {port}")
        return True
    except Exception as e:
        logging.error(f"Failed to connect to {port}: {str(e)}")
        return False

def on_connection(interface, topic=pub.AUTO_TOPIC):
    logging.info("Connected to Meshtastic device")
    # Perform any necessary actions upon connection
    interface.sendText("Connected to Meshtastic device")
    info = interface.getMyNodeInfo()
    logging.info(f"Connected to {info}")
    
    # Send channel information to web interface via server-sent events
    radio_config = mesh_pb2.Config()
    interface._getRadioConfig(radio_config)
    channels = radio_config.channel_settings
    for channel in channels:
        channel_data = {
            "index": channel.index,
            "name": channel.name
        }
        sse_data = f"event: channel_info\ndata: {json.dumps(channel_data)}\n\n"
        for queue in app.sse_queues:
            queue.put(sse_data)
            
def on_receive(packet, interface):
    logging.debug(f"Received: {packet}")
    # Handle received packet based on its type
    if "decoded" in packet:
        portnum = packet["decoded"]["portnum"]
        if portnum == portnums_pb2.PortNum.TEXT_MESSAGE_APP:
            handle_text_message(packet, interface)
        elif portnum == portnums_pb2.PortNum.POSITION_APP:
            handle_position_message(packet, interface)
        elif portnum == portnums_pb2.PortNum.NODEINFO_APP:
            handle_nodeinfo_message(packet, interface)
        elif portnum == portnums_pb2.PortNum.TELEMETRY_APP:
            handle_telemetry_message(packet, interface)
        # Add more conditions for other packet types as needed
        else:
            logging.warning(f"Unhandled portnum: {portnum}")
    else:
        logging.warning("Received packet without 'decoded' key")

def handle_text_message(packet, interface):
    # Handle text message packet
    try:
        text = packet["decoded"].get("text", "")
        sender = packet.get("from", "Unknown")
        logging.info(f"Received text message from {sender}: {text}")
        # Prepare text message data for web interface
        message_data = {
            "sender": sender,
            "text": text
        }
        # Send text message data to web interface via server-sent events
        sse_data = f"event: text_message\ndata: {json.dumps(message_data)}\n\n"
        for queue in app.sse_queues:
            queue.put(sse_data)
    except (KeyError, TypeError) as e:
        logging.error(f"Error handling text message: {str(e)}")

def handle_position_message(packet, interface):
    # Handle position message packet
    position = packet["decoded"]["position"]
    sender = packet.get("from", "Unknown")
    latitude = position.get("latitudeI", 0) / 1e7
    longitude = position.get("longitudeI", 0) / 1e7
    altitude = position.get("altitude", 0)
    logging.info(f"Received position from {sender}: Lat: {latitude}, Lon: {longitude}, Alt: {altitude}")
    # Perform any necessary actions with the received position data
    interface.nodes[sender]["position"] = position

def handle_nodeinfo_message(packet, interface):
    # Handle nodeinfo message packet
    nodeinfo = packet["decoded"]["user"]
    sender = packet.get("from", "Unknown")
    logging.info(f"Received nodeinfo from {sender}: {nodeinfo}")
    
    # Prepare user data for web interface
    user_data = {
        "sender": sender,
        "id": nodeinfo.get("id", ""),
        "longName": nodeinfo.get("longName", ""),
        "shortName": nodeinfo.get("shortName", ""),
        "macaddr": nodeinfo.get("macaddr", ""),
        "hwModel": nodeinfo.get("hwModel", "")
    }
    
    # Send user data to web interface via server-sent events
    sse_data = f"event: user_data\ndata: {json.dumps(user_data)}\n\n"
    for queue in app.sse_queues:
        queue.put(sse_data)

def handle_telemetry_message(packet, interface):
    # Handle telemetry message packet
    telemetry = packet["decoded"].get("telemetry", {})
    sender = packet.get("from", "Unknown")
    logging.info(f"Received telemetry from {sender}: {telemetry}")
    
    # Extract relevant telemetry data
    battery_level = telemetry.get("deviceMetrics", {}).get("batteryLevel", 0)
    voltage = telemetry.get("deviceMetrics", {}).get("voltage", 0.0)
    channel_utilization = telemetry.get("deviceMetrics", {}).get("channelUtilization", 0.0)
    air_util_tx = telemetry.get("deviceMetrics", {}).get("airUtilTx", 0.0)
    uptime_seconds = telemetry.get("deviceMetrics", {}).get("uptimeSeconds", 0)
    
    # Prepare telemetry data for web interface
    telemetry_data = {
        "sender": sender,
        "batteryLevel": battery_level,
        "voltage": voltage,
        "channelUtilization": channel_utilization,
        "airUtilTx": air_util_tx,
        "uptimeSeconds": uptime_seconds
    }
    
    # Send telemetry data to web interface via server-sent events
    sse_data = f"event: telemetry_data\ndata: {json.dumps(telemetry_data)}\n\n"
    for queue in app.sse_queues:
        queue.put(sse_data)

def handle_position_message(packet, interface):
    # Handle position message packet
    position = packet["decoded"].get("position", {})
    sender = packet.get("from", "Unknown")
    latitude = position.get("latitude", 0.0)
    longitude = position.get("longitude", 0.0)
    altitude = position.get("altitude", 0)
    logging.info(f"Received position from {sender}: Lat: {latitude}, Lon: {longitude}, Alt: {altitude}")
    
    # Update node position in the interface
    interface.nodes[sender]["position"] = position
    
    # Prepare position data for web interface
    position_data = {
        "sender": sender,
        "latitude": latitude,
        "longitude": longitude,
        "altitude": altitude
    }
    
    # Send position data to web interface via server-sent events
    sse_data = f"event: position_data\ndata: {json.dumps(position_data)}\n\n"
    for queue in app.sse_queues:
        queue.put(sse_data)

def handle_command(command, sender, interface):
    if command == "!info":
        info = interface.getMyNodeInfo()
        response = f"Device info: {info}"
        interface.sendText(response, destinationId=sender)
    elif command.startswith("!send"):
        _, dest, message = command.split(" ", 2)
        interface.sendText(message, destinationId=dest)
    elif command.startswith("!setprofile"):
        _, profile_name = command.split(" ", 1)
        set_device_profile(profile_name, interface)
    # Add more command handlers as needed
    else:
        logging.warning(f"Unknown command: {command}")

def set_device_profile(profile_name, interface):
    # Create a new DeviceProfile message
    profile = clientonly_pb2.DeviceProfile()
    
    # Set the profile fields based on the profile name
    if profile_name == "default":
        profile.long_name = "Default Profile"
        profile.short_name = "DEFAULT"
        profile.channel_url = ""
        # Set other profile fields as needed
    elif profile_name == "custom":
        profile.long_name = "Custom Profile"
        profile.short_name = "CUSTOM"
        profile.channel_url = "https://example.com/custom_channel.json"
        # Set other profile fields as needed
    else:
        logging.warning(f"Unknown profile name: {profile_name}")
        return
    
    # Set the device configuration
    interface.sendSimplePacket(profile.SerializeToString(), portnums_pb2.PortNum.ADMIN_APP, localOnly=True)
    logging.info(f"Device profile set to {profile_name}")


@app.route('/')
def index():
    ports = list_serial_ports()
    return render_template('index.html', ports=ports, connected=serial_interface is not None)

@app.route('/connect', methods=['POST'])
def connect():
    port = request.form['port']
    if open_serial_connection(port):
        return {'status': 'connected', 'port': port}
    else:
        return {'status': 'error', 'message': 'Failed to connect to port'}
    
@app.route('/disconnect', methods=['POST'])
def disconnect():
    if serial_interface:
        try:
            serial_interface.close()
            logging.info('Serial connection closed')
            return {'status': 'disconnected'}
        except Exception as e:
            logging.error(f"Error closing serial connection: {str(e)}")
            return {'status': 'error', 'message': 'Failed to close serial connection'}
    else:
        return {'status': 'error', 'message': 'No serial connection'}

@app.route('/send_message', methods=['POST'])
def send_message():
    message = request.form['message']
    channel = int(request.form['channel'])
    if serial_interface:
        try:
            serial_interface.sendText(message, wantAck=True, channelIndex=channel)
            logging.info(f"Message sent on channel {channel}: {message}")
            return {'status': 'sent', 'message': message}
        except Exception as e:
            logging.error(f"Error sending message: {str(e)}")
            return {'status': 'error', 'message': 'Failed to send message'}
    else:
        return {'status': 'error', 'message': 'No serial connection'}
    
@app.route('/stream')
def stream():
    def event_stream():
        event_queue = queue_module.Queue()
        app.sse_queues.append(event_queue)
        try:
            while True:
                data = event_queue.get()
                yield data
        except GeneratorExit:
            app.sse_queues.remove(event_queue)

    return Response(stream_with_context(event_stream()), mimetype='text/event-stream')    

if __name__ == '__main__':
    try:
        available_ports = list_serial_ports()
        if available_ports:
            logging.info(f"Available ports: {available_ports}")
        else:
            logging.warning("No COM ports detected at startup.")
        
        app.run(host='127.0.0.1', port=5678)
    except Exception as e:
        logging.critical(f"Unhandled exception occurred: {str(e)}")

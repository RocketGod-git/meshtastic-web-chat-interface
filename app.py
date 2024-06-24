from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import serial
import serial.tools.list_ports
import json
import logging
import meshtastic
from meshtastic.serial_interface import SerialInterface
from meshtastic import mesh_pb2, portnums_pb2, telemetry_pb2
from pubsub import pub
import time
from datetime import datetime
import asyncio

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

available_channels = []
found_nodes = {}
received_messages = []
app.config['messages'] = {}

def list_serial_ports():
    try:
        return [port.device for port in serial.tools.list_ports.comports()]
    except Exception as e:
        logging.error(f"Error listing serial ports: {e}")
        return []

def connect_serial(port):
    try:
        interface = SerialInterface(port)
        logging.info(f"Interface created: {interface}")
        logging.info(f"Interface type: {type(interface)}")
        logging.info(f"Interface attributes: {dir(interface)}")

        # interface.sendText("Connected to Meshtastic device")
        pub.subscribe(on_receive, "meshtastic.receive")
        pub.subscribe(on_connection, "meshtastic.connection.established")
        
        socketio.emit('serial_connected', {'port': port})
        logging.info(f"Connected to Meshtastic device on {port}")
        return interface
    except Exception as e:
        logging.error(f"Error connecting to Meshtastic device: {e}")
        socketio.emit('serial_error', {'message': str(e)})
        return None

def on_connection(interface, topic=pub.AUTO_TOPIC):
    global available_channels, received_messages
    pub.subscribe(on_receive, "meshtastic.receive")
    pub.subscribe(on_connection, "meshtastic.connection.established")
    logging.info("Connected to Meshtastic device")
    try:
        info = interface.getMyNodeInfo()
        logging.info(f"Connected to {info}")

        channels = interface.localNode.channels

        available_channels = []
        for idx, channel in enumerate(channels):
            channel_info = {
                'index': idx,
                'name': channel.settings.name or f'Channel {idx}',
                'role': channel.role
            }
            available_channels.append(channel_info)

        logging.info(f"Available channels: {available_channels}")
        socketio.emit('channel_list', {'channels': available_channels})

        nodes = interface.nodes
        for node_id, node_data in nodes.items():
            update_node(node_id, node_data)

        socketio.emit('all_messages', {'messages': received_messages})

    except Exception as e:
        logging.error(f"Error in on_connection: {e}")
        logging.exception("Stack trace:")
        default_channel = {'index': 0, 'name': 'Default', 'role': 'PRIMARY'}
        available_channels = [default_channel]
        socketio.emit('channel_list', {'channels': available_channels})
        
    except Exception as e:
        logging.error(f"Error in on_connection: {e}")
        logging.exception("Stack trace:")
        default_channel = {'index': 0, 'name': 'Default', 'role': 'PRIMARY'}
        available_channels = [default_channel]
        socketio.emit('channel_list', {'channels': available_channels})

def on_receive(packet, interface):
    logging.debug(f"Raw received packet: {packet}")
    try:
        if 'decoded' in packet:
            portnum = packet['decoded'].get('portnum')
            if portnum == 'TEXT_MESSAGE_APP':
                handle_text_message(packet['decoded'], packet)
            elif portnum == 'NODEINFO_APP':
                handle_nodeinfo_message(packet['decoded'], packet)
            elif portnum == 'POSITION_APP':
                handle_position_message(packet['decoded'], packet)
            elif portnum == 'TELEMETRY_APP':
                handle_telemetry_message(packet['decoded'], packet)
            elif portnum == 'ADMIN_APP':
                handle_admin_message(packet['decoded'], packet)
            else:
                logging.warning(f"Unhandled portnum: {portnum}")
        elif 'fromId' in packet and packet.get('decoded', {}).get('portnum') == 'ADMIN_APP':
            handle_admin_message(packet['decoded'], packet)
        elif 'encrypted' in packet:
            logging.info("Received encrypted packet")
        else:
            logging.warning("Received packet with unknown format")
        
        update_node(packet.get('fromId'), packet)
    except Exception as e:
        logging.error(f"Unexpected error in on_receive: {e}")
        logging.exception("Stack trace:")

def handle_admin_message(decoded_data, packet):
    try:
        if decoded_data.get('payload'):
            admin_message = mesh_pb2.AdminMessage()
            admin_message.ParseFromString(decoded_data['payload'])
            if admin_message.get_ack:
                ack_packet_id = admin_message.get_ack.for_packet
                logging.info(f"ACK received for packet ID: {ack_packet_id}")
                socketio.emit('message_ack', {'packetId': ack_packet_id})
    except Exception as e:
        logging.error(f"Error in handle_admin_message: {e}")
        logging.exception("Stack trace:")

def handle_routing_message(decoded_data, packet):
    try:
        if decoded_data.get('requestId'):
            ack_packet_id = decoded_data.get('requestId')
            logging.info(f"ACK received for packet ID: {ack_packet_id}")
            socketio.emit('message_ack', {'packetId': ack_packet_id})
    except Exception as e:
        logging.error(f"Error in handle_routing_message: {e}")
        logging.exception("Stack trace:")

def update_node(node_id, data):
    global found_nodes
    if not node_id:
        node_id = data.get('from') or data.get('fromId')
    if not node_id:
        logging.warning("Attempted to update node with empty ID")
        return
    
    if node_id not in found_nodes:
        found_nodes[node_id] = {}
    
    node_info = {
        'num': node_id,
        'user': {
            'id': data.get('fromId') or data.get('user', {}).get('id'),
            'longName': data.get('user', {}).get('longName'),
            'shortName': data.get('user', {}).get('shortName'),
            'macaddr': data.get('user', {}).get('macaddr'),
            'hwModel': data.get('user', {}).get('hwModel'),
            'isLicensed': data.get('user', {}).get('isLicensed'),
            'role': data.get('user', {}).get('role')
        },
        'position': {
            'latitude': data.get('position', {}).get('latitude'),
            'longitude': data.get('position', {}).get('longitude'),
            'altitude': data.get('position', {}).get('altitude'),
            'time': data.get('position', {}).get('time'),
            'latitudeI': data.get('position', {}).get('latitudeI'),
            'longitudeI': data.get('position', {}).get('longitudeI')
        },
        'snr': data.get('rxSnr') or data.get('snr'),
        'lastHeard': data.get('rxTime') or data.get('lastHeard'),
        'deviceMetrics': data.get('deviceMetrics') or {},
        'hopsAway': data.get('hopStart') or data.get('hopsAway'),
        'telemetry': data.get('telemetry') or {}
    }
    
    node_info = {k: v for k, v in node_info.items() if v is not None}
    node_info['user'] = {k: v for k, v in node_info['user'].items() if v is not None}
    node_info['position'] = {k: v for k, v in node_info['position'].items() if v is not None}
    
    found_nodes[node_id] = merge_dicts(found_nodes[node_id], node_info)
    
    logging.info(f"Node updated: {found_nodes[node_id]}")
    socketio.emit('node_updated', found_nodes[node_id])

def merge_dicts(dict1, dict2):
    result = dict1.copy()
    for key, value in dict2.items():
        if isinstance(value, dict):
            result[key] = merge_dicts(result.get(key, {}), value)
        elif value is not None:
            result[key] = value
    return result

def handle_decoded_packet(decoded_data, packet, interface):
    try:
        portnum = decoded_data.get("portnum")

        if portnum == portnums_pb2.PortNum.TEXT_MESSAGE_APP:
            handle_text_message(decoded_data, packet)
        elif portnum == portnums_pb2.PortNum.NODEINFO_APP:
            handle_nodeinfo_message(decoded_data, packet)
        elif portnum == portnums_pb2.PortNum.TELEMETRY_APP:
            handle_telemetry_message(decoded_data, packet)
        else:
            logging.warning(f"Unhandled portnum: {portnum}")
    except Exception as e:
        logging.error(f"Error in handle_decoded_packet: {e}")
        logging.exception("Stack trace:")

def handle_text_message(decoded_data, packet):
    try:
        logging.info(f"Raw text message packet: {packet}")

        sender_id = packet.get('fromId', 'Unknown')
        sender_node = found_nodes.get(sender_id, {})
        sender_name = sender_node.get('user', {}).get('longName') or sender_node.get('user', {}).get('shortName') or sender_id

        message = {
            'sender': sender_name,
            'text': decoded_data.get('text', ''),
            'channel': packet.get('channel', 0),  
            'timestamp': packet.get('rxTime', int(time.time()))
        }

        display_message = f"{sender_name}\n{message['text']}\n{datetime.fromtimestamp(message['timestamp']).strftime('%Y-%m-%d %H:%M:%S')}"

        received_messages.append(message)
        logging.info(f"New text message received: {display_message}")
        socketio.emit('new_message', {'raw_message': message, 'formatted_message': display_message})
    except Exception as e:
        logging.error(f"Error in handle_text_message: {e}")
        logging.exception("Stack trace:")

def handle_position_message(decoded_data, packet):
    try:
        position = decoded_data.get('position', {})
        position_data = {
            'sender': packet.get('fromId', 'Unknown'),
            'latitude': position.get('latitude', 0),
            'longitude': position.get('longitude', 0),
            'altitude': position.get('altitude', 0),
            'timestamp': packet.get('rxTime', 0)
        }
        logging.info(f"Position update: {position_data}")
        socketio.emit('position_update', position_data)
    except Exception as e:
        logging.error(f"Error in handle_position_message: {e}")
        logging.exception("Stack trace:")

def handle_nodeinfo_message(decoded_data, packet):
    try:
        payload = mesh_pb2.User()
        payload.ParseFromString(decoded_data.get('payload'))
        node_info = {
            'user': {
                'id': payload.id,
                'longName': payload.long_name,
                'shortName': payload.short_name,
                'macaddr': payload.macaddr.hex(),
                'hwModel': payload.hw_model,
                'isLicensed': payload.is_licensed,
                'role': payload.role if hasattr(payload, 'role') else None
            },
            'num': packet.get('fromId') or packet.get('from') or payload.id,
            'snr': packet.get('rxSnr'),
            'lastHeard': packet.get('rxTime'),
            'hopsAway': packet.get('hopStart')
        }
        
        # Position information
        if hasattr(payload, 'latitude') and hasattr(payload, 'longitude'):
            node_info['position'] = {
                'latitude': payload.latitude,
                'longitude': payload.longitude,
                'latitudeI': int(payload.latitude * 1e7) if payload.latitude is not None else None,
                'longitudeI': int(payload.longitude * 1e7) if payload.longitude is not None else None,
                'altitude': payload.altitude if hasattr(payload, 'altitude') else None,
                'time': payload.last_heard if hasattr(payload, 'last_heard') else None
            }
        
        # Device metrics
        node_info['deviceMetrics'] = {}
        if hasattr(payload, 'battery_level'):
            node_info['deviceMetrics']['batteryLevel'] = payload.battery_level
        if hasattr(payload, 'voltage'):
            node_info['deviceMetrics']['voltage'] = payload.voltage
        if hasattr(payload, 'channel_utilization'):
            node_info['deviceMetrics']['channelUtilization'] = payload.channel_utilization
        if hasattr(payload, 'air_util_tx'):
            node_info['deviceMetrics']['airUtilTx'] = payload.air_util_tx
        if hasattr(payload, 'snr'):
            node_info['deviceMetrics']['snr'] = payload.snr
        if hasattr(payload, 'uptime_seconds'):
            node_info['deviceMetrics']['uptimeSeconds'] = payload.uptime_seconds

        # Telemetry (if available)
        if hasattr(payload, 'telemetry'):
            node_info['telemetry'] = {
                'time': payload.telemetry.time,
                'deviceMetrics': {
                    'batteryLevel': payload.telemetry.device_metrics.battery_level,
                    'voltage': payload.telemetry.device_metrics.voltage,
                    'channelUtilization': payload.telemetry.device_metrics.channel_utilization,
                    'airUtilTx': payload.telemetry.device_metrics.air_util_tx,
                    'uptimeSeconds': payload.telemetry.device_metrics.uptime_seconds
                }
            }
            if hasattr(payload.telemetry, 'environment'):
                node_info['telemetry']['environment'] = {
                    'temperature': payload.telemetry.environment.temperature,
                    'relativeHumidity': payload.telemetry.environment.relative_humidity,
                    'barometricPressure': payload.telemetry.environment.barometric_pressure,
                    'gasResistance': payload.telemetry.environment.gas_resistance,
                    'voltage': payload.telemetry.environment.voltage,
                    'current': payload.telemetry.environment.current,
                    'satelliteCount': payload.telemetry.environment.satellite_count
                }

        node_info = {k: v for k, v in node_info.items() if v}
        node_info['user'] = {k: v for k, v in node_info['user'].items() if v is not None}
        if 'position' in node_info:
            node_info['position'] = {k: v for k, v in node_info['position'].items() if v is not None}
        if 'deviceMetrics' in node_info:
            node_info['deviceMetrics'] = {k: v for k, v in node_info['deviceMetrics'].items() if v is not None}
        if 'telemetry' in node_info:
            node_info['telemetry'] = {k: v for k, v in node_info['telemetry'].items() if v}
            if 'deviceMetrics' in node_info['telemetry']:
                node_info['telemetry']['deviceMetrics'] = {k: v for k, v in node_info['telemetry']['deviceMetrics'].items() if v is not None}
            if 'environment' in node_info['telemetry']:
                node_info['telemetry']['environment'] = {k: v for k, v in node_info['telemetry']['environment'].items() if v is not None}

        update_node(node_info['num'], node_info)
        
        logging.info(f"Node info received for {payload.id}: {node_info}")
    except Exception as e:
        logging.error(f"Error in handle_nodeinfo_message: {e}")
        logging.exception("Stack trace:")

def handle_telemetry_message(decoded_data, packet):
    try:
        payload = telemetry_pb2.Telemetry()
        payload.ParseFromString(decoded_data.get('payload'))
        telemetry_data = {
            'time': payload.time,
            'deviceMetrics': {
                'batteryLevel': payload.device_metrics.battery_level,
                'voltage': payload.device_metrics.voltage,
                'channelUtilization': payload.device_metrics.channel_utilization,
                'airUtilTx': payload.device_metrics.air_util_tx,
                'uptimeSeconds': payload.device_metrics.uptime_seconds
            }
        }
        if payload.HasField('environment'):
            telemetry_data['environment'] = {
                'temperature': payload.environment.temperature,
                'relativeHumidity': payload.environment.relative_humidity,
                'barometricPressure': payload.environment.barometric_pressure,
                'gasResistance': payload.environment.gas_resistance,
                'voltage': payload.environment.voltage,
                'current': payload.environment.current,
                'satelliteCount': payload.environment.satellite_count
            }
        update_node(packet.get('fromId') or packet.get('from'), {'telemetry': telemetry_data})
    except Exception as e:
        logging.error(f"Error in handle_telemetry_message: {e}")
        logging.exception("Stack trace:")

def handle_ack_message(decoded_data, packet):
    try:
        ack_packet_id = decoded_data.get('requestId')
        logging.info(f"ACK received for packet ID: {ack_packet_id}")
        socketio.emit('message_ack', {'packetId': ack_packet_id})
    except Exception as e:
        logging.error(f"Error in handle_ack_message: {e}")
        logging.exception("Stack trace:")

@app.route('/')
def index():
    try:
        ports = list_serial_ports()
        return render_template('index.html', ports=ports, channels=available_channels, nodes=found_nodes)
    except Exception as e:
        logging.error(f"Error in index route: {e}")
        logging.exception("Stack trace:")
        return "An error occurred", 500

@socketio.on('connect')
def handle_connect():
    logging.info("WebSocket client connected")

@socketio.on('disconnect')
def handle_disconnect():
    logging.info("WebSocket client disconnected")

@socketio.on('connect_serial')
def handle_connect_serial(data):
    try:
        port = data.get('port')
        interface = connect_serial(port)
        if interface:
            app.config['serial_interface'] = interface
            on_connection(interface)
        else:
            socketio.emit('serial_error', {'message': 'Failed to connect'})
    except Exception as e:
        logging.error(f"Error in connect_serial: {e}")
        logging.exception("Stack trace:")
        socketio.emit('serial_error', {'message': str(e)})

@socketio.on('disconnect_serial')
def handle_disconnect_serial():
    try:
        interface = app.config.get('serial_interface')
        if interface:
            interface.close()
            app.config['serial_interface'] = None
            logging.info("Disconnected from Meshtastic device")
            socketio.emit('serial_disconnected')
        else:
            socketio.emit('serial_error', {'message': 'Not connected'})
    except Exception as e:
        logging.error(f"Error in disconnect_serial: {e}")
        logging.exception("Stack trace:")
        socketio.emit('serial_error', {'message': str(e)})

@socketio.on('send_message')
def handle_send_message(data):
    try:
        message = data.get('message')
        channel_index = data.get('channel', 0)
        interface = app.config.get('serial_interface')
        if interface:
            mesh_packet = interface.sendText(message, channelIndex=channel_index, wantAck=True)
            packet_id = mesh_packet.id
            logging.info(f"Sent message: {message} on channel {channel_index} with packet ID: {packet_id}")
            
            sent_message = {
                'sender': 'You',  
                'text': message,
                'channel': channel_index,
                'timestamp': int(time.time()),
                'packetId': packet_id,
                'status': 'pending'
            }
            
            if 'messages' not in app.config:
                app.config['messages'] = {}
            
            if channel_index not in app.config['messages']:
                app.config['messages'][channel_index] = []
            app.config['messages'][channel_index].append(sent_message)
            
            socketio.emit('new_message', {'raw_message': sent_message})
            logging.debug(f"Emitted new_message event for sent message: {sent_message}")
            
            socketio.emit('message_sent', {'status': 'success', 'packetId': packet_id})
            
            socketio.start_background_task(wait_for_ack, interface, packet_id)
        else:
            socketio.emit('serial_error', {'message': 'Not connected'})
    except Exception as e:
        logging.error(f"Error in send_message: {str(e)}")
        logging.exception("Stack trace:")
        socketio.emit('serial_error', {'message': str(e)})

def wait_for_ack(interface, packet_id):
    try:
        ack = interface.waitForAckNak(packet_id, 30)  
        if ack:
            logging.info(f"ACK received for packet ID: {packet_id}")
            socketio.emit('message_ack', {'packetId': packet_id})
        else:
            logging.warning(f"ACK timeout for packet ID: {packet_id}")
            socketio.emit('message_ack_timeout', {'packetId': packet_id})
    except Exception as e:
        logging.error(f"Error waiting for ACK: {str(e)}")

if __name__ == '__main__':
    socketio.run(app, port=5678)

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS

import serial
import serial.tools.list_ports

import json
import logging
import os

import meshtastic
from meshtastic.serial_interface import SerialInterface
from meshtastic import mesh_pb2, portnums_pb2, telemetry_pb2
from meshtastic.mesh_interface import MeshInterface
from meshtastic import mesh_interface

from pubsub import pub

import time
from datetime import datetime
import asyncio
import threading
from threading import Event
import requests

from google.protobuf.json_format import MessageToDict

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

available_channels = []
found_nodes = {}
received_messages = []
messages = {}

app.config['received_messages'] = received_messages
app.config['messages'] = {}

connection_timeout = None

WEBHOOK_FILE = 'discord_webhook.json'

def list_serial_ports():
    try:
        return [port.device for port in serial.tools.list_ports.comports()]
    except Exception as e:
        logging.error(f"Error listing serial ports: {e}")
        return []

def connect_serial(port):
    global connection_timeout
    
    try:
        def timeout_handler():
            logging.error("Timed out waiting for connection completion")
            safe_emit('serial_error', {'message': 'Connection timed out'})

        connection_timeout = threading.Timer(60, timeout_handler)
        connection_timeout.start()

        interface = SerialInterface(port)
        logging.info(f"Interface created: {interface}")

        if connection_timeout:
            connection_timeout.cancel()
            connection_timeout = None

        pub.subscribe(on_receive, "meshtastic.receive")
        pub.subscribe(on_connection, "meshtastic.connection.established")
        
        interface.on_received = on_receive

        my_info = interface.getMyNodeInfo()
        app.config['my_node_id'] = my_info['num']
        
        initial_node_info = {
            'num': my_info['num'],
            'user': {
                'id': my_info['user']['id'],
                'longName': my_info['user']['longName'],
                'shortName': my_info['user']['shortName'],
                'macaddr': my_info['user']['macaddr'],
                'hwModel': my_info['user']['hwModel']
            },
            'position': my_info.get('position', {}),
            'lastHeard': my_info.get('lastHeard'),
            'deviceMetrics': my_info.get('deviceMetrics', {}),
            'isLocal': True
        }

        update_node(initial_node_info['num'], initial_node_info)

        safe_emit('serial_connected', {'port': port, 'initialNodeInfo': initial_node_info})
        logging.info(f"Connected to Meshtastic device on {port}")
        return interface
    except Exception as e:
        if connection_timeout:
            connection_timeout.cancel()
            connection_timeout = None
        logging.error(f"Error connecting to Meshtastic device: {e}")
        safe_emit('serial_error', {'message': str(e)})
        return None

def on_connection(interface, topic=pub.AUTO_TOPIC):
    global connection_timeout, available_channels, received_messages
    if connection_timeout:
        connection_timeout.cancel()
        connection_timeout = None

    pub.subscribe(on_receive, "meshtastic.receive")
    pub.subscribe(on_connection, "meshtastic.connection.established")
    logging.info("Connected to Meshtastic device")
    try:
        clear_message_queue(interface)

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

        received_messages = fetch_stored_messages(interface)
        app.config['received_messages'] = received_messages
        socketio.emit('all_messages', {'messages': received_messages})

    except Exception as e:
        logging.error(f"Error in on_connection: {e}")
        logging.exception("Stack trace:")
        default_channel = {'index': 0, 'name': 'Default', 'role': 'PRIMARY'}
        available_channels = [default_channel]
        socketio.emit('channel_list', {'channels': available_channels})

def safe_emit(event, data):
    try:
        socketio.emit(event, data)
    except Exception as e:
        logging.error(f"Error emitting {event}: {e}")

def fetch_stored_messages(interface):
    messages = []
    try:
        if hasattr(interface, 'nodesByNum'):
            nodes = interface.nodesByNum
            for node_id, node_data in nodes.items():
                if 'messages' in node_data:
                    for msg in node_data['messages']:
                        message = {
                            'from': msg.get('fromId'),
                            'to': msg.get('toId'),
                            'payload': msg.get('payload'),
                            'portnum': msg.get('portnum'),
                            'timestamp': msg.get('rxTime')
                        }
                        messages.append(message)
        else:
            logging.warning("Interface does not have attribute 'nodesByNum'")
    except Exception as e:
        logging.error(f"Error fetching stored messages: {e}")
        logging.exception("Stack trace:")
    return messages

def clear_message_queue(interface):
    try:
        queue_status = interface.getQueueStatus()
        logging.info(f"Initial queue status: {queue_status}")

        while queue_status.free < queue_status.maxlen:
            interface.deleteFromQueue()
            queue_status = interface.getQueueStatus()
            logging.info(f"Cleared a message. New queue status: {queue_status}")

        logging.info("Message queue cleared successfully")
        socketio.emit('queue_cleared', {'message': 'Message queue cleared on connection'})
    except Exception as e:
        logging.error(f"Error clearing message queue: {e}")
        logging.exception("Stack trace:")
        socketio.emit('queue_error', {'message': f'Error clearing queue: {str(e)}'})

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
            elif portnum == 'ROUTING_APP':
                handle_routing_message(packet['decoded'], packet)
            elif 'encrypted' in packet:
                logging.info(f"Received encrypted packet from {packet.get('fromId') or packet.get('from')} to {packet.get('toId') or packet.get('to')}")
                logging.debug(f"Encrypted packet details: Channel: {packet.get('channel')}, SNR: {packet.get('rxSnr')}, RSSI: {packet.get('rxRssi')}, Hops: {packet.get('hopStart')}")    
            else:
                logging.warning(f"Unhandled portnum: {portnum}")
            
            if 'message' in packet['decoded']:
                update_messages(packet['decoded'], packet)
        elif 'encrypted' in packet:
            logging.info("Received encrypted packet")
        else:
            logging.warning("Received packet with unknown format")
        
        update_node(packet.get('fromId') or packet.get('from'), packet)
    except Exception as e:
        logging.error(f"Unexpected error in on_receive: {e}")
        logging.exception("Stack trace:")

def update_messages(decoded_data, packet):
    try:
        global received_messages
        message = {
            'from': packet.get('fromId'),
            'to': packet.get('toId'),
            'payload': decoded_data.get('payload'),
            'portnum': decoded_data.get('portnum'),
            'timestamp': packet.get('rxTime')
        }
        received_messages.append(message)
        logging.info(f"New message added: {message}")
        socketio.emit('new_message', {'message': message})
    except Exception as e:
        logging.error(f"Error updating messages: {e}")
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
        found_nodes[node_id] = {'num': node_id}
    
    node_info = {
        'num': node_id,
        'user': data.get('user', {}),
        'position': data.get('position', {}),
        'snr': data.get('snr') or data.get('rxSnr'),
        'lastHeard': data.get('lastHeard') or data.get('rxTime'),
        'deviceMetrics': data.get('deviceMetrics', {}),
        'hopsAway': data.get('hopsAway') or data.get('hopStart'),
        'telemetry': data.get('telemetry', {}),
        'viaMqtt': data.get('viaMqtt'),
        'lastUpdated': int(time.time())  
    }
    
    found_nodes[node_id] = merge_dicts(found_nodes[node_id], node_info)
    
    found_nodes[node_id] = {k: v for k, v in found_nodes[node_id].items() if v is not None}
    for key in ['user', 'position', 'deviceMetrics', 'telemetry']:
        if key in found_nodes[node_id]:
            found_nodes[node_id][key] = {k: v for k, v in found_nodes[node_id][key].items() if v is not None}
    
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
            'latitude': position.get('latitude'),
            'longitude': position.get('longitude'),
            'latitudeI': position.get('latitudeI'),
            'longitudeI': position.get('longitudeI'),
            'altitude': position.get('altitude'),
            'time': position.get('time'),
            'timestamp': packet.get('rxTime', 0),
            'PDOP': position.get('PDOP'),
            'groundSpeed': position.get('groundSpeed'),
            'groundTrack': position.get('groundTrack'),
            'satsInView': position.get('satsInView'),
            'precisionBits': position.get('precisionBits')
        }
        logging.info(f"Position update: {position_data}")
        socketio.emit('position_update', position_data)
        
        node_info = {
            'position': position_data,
            'lastHeard': packet.get('rxTime'),
            'snr': packet.get('rxSnr'),
            'hopsAway': packet.get('hopStart')
        }
        update_node(packet.get('fromId') or packet.get('from'), node_info)
    except Exception as e:
        logging.error(f"Error in handle_position_message: {e}")
        logging.exception("Stack trace:")

def handle_telemetry_message(decoded_data, packet):
    try:
        telemetry = decoded_data.get('telemetry', {})
        telemetry_data = {
            'time': telemetry.get('time'),
            'deviceMetrics': telemetry.get('deviceMetrics', {})
        }
        
        telemetry_data['deviceMetrics'] = {k: v for k, v in telemetry_data['deviceMetrics'].items() if v is not None}
        
        node_info = {
            'telemetry': telemetry_data,
            'lastHeard': packet.get('rxTime'),
            'snr': packet.get('rxSnr'),
            'hopsAway': packet.get('hopStart')
        }
        update_node(packet.get('fromId') or packet.get('from'), node_info)
    except Exception as e:
        logging.error(f"Error in handle_telemetry_message: {e}")
        logging.exception("Stack trace:")

def handle_nodeinfo_message(decoded_data, packet):
    try:
        user_data = decoded_data.get('user', {})
        node_info = {
            'num': packet.get('fromId') or packet.get('from') or user_data.get('id'),
            'user': {
                'id': user_data.get('id'),
                'longName': user_data.get('longName'),
                'shortName': user_data.get('shortName'),
                'macaddr': user_data.get('macaddr'),
                'hwModel': user_data.get('hwModel'),
                'isLicensed': user_data.get('isLicensed'),
                'role': user_data.get('role')
            },
            'position': {
                'latitude': user_data.get('latitude'),
                'longitude': user_data.get('longitude'),
                'latitudeI': user_data.get('latitudeI'),
                'longitudeI': user_data.get('longitudeI'),
                'altitude': user_data.get('altitude'),
                'time': user_data.get('time')
            },
            'snr': packet.get('rxSnr'),
            'lastHeard': packet.get('rxTime'),
            'hopsAway': packet.get('hopStart'),
            'deviceMetrics': {
                'batteryLevel': user_data.get('batteryLevel'),
                'voltage': user_data.get('voltage'),
                'channelUtilization': user_data.get('channelUtilization'),
                'airUtilTx': user_data.get('airUtilTx'),
                'snr': user_data.get('snr')
            }
        }

        if 'position' in decoded_data:
            position = decoded_data['position']
            node_info['position'].update({
                'PDOP': position.get('PDOP'),
                'groundSpeed': position.get('groundSpeed'),
                'groundTrack': position.get('groundTrack'),
                'satsInView': position.get('satsInView'),
                'precisionBits': position.get('precisionBits')
            })

        node_info = {k: v for k, v in node_info.items() if v is not None}
        node_info['user'] = {k: v for k, v in node_info['user'].items() if v is not None}
        node_info['position'] = {k: v for k, v in node_info['position'].items() if v is not None}
        node_info['deviceMetrics'] = {k: v for k, v in node_info['deviceMetrics'].items() if v is not None}

        update_node(node_info['num'], node_info)
        
        logging.info(f"Node info received for {user_data.get('id')}: {node_info}")
    except Exception as e:
        logging.error(f"Error in handle_nodeinfo_message: {e}")
        logging.exception("Stack trace:")


@app.route('/')
def index():
    try:
        ports = list_serial_ports()
        return render_template('index.html', 
                               ports=ports, 
                               initialChannels=available_channels, 
                               initialNodes=found_nodes,
                               initialMessages=messages)
    except Exception as e:
        logging.error(f"Error in index route: {e}")
        logging.exception("Stack trace:")
        return render_template('error.html', error_message="An error occurred while loading the page"), 500

@app.route('/settings')
def settings():
    return render_template('settings.html')


@socketio.on_error()
def error_handler(e):
    logging.error(f"SocketIO error: {e}")
    safe_emit('error', {'message': 'An unexpected error occurred'})


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

@socketio.on('get_messages')
def handle_get_messages():
    try:
        socketio.emit('all_messages', {'messages': app.config['received_messages']})
    except Exception as e:
        logging.error(f"Error in get_messages: {e}")
        logging.exception("Stack trace:")

@socketio.on('send_message')
def handle_send_message(data):
    try:
        message = data.get('message')
        channel_index = data.get('channel', 0)
        interface = app.config.get('serial_interface')
        if interface:
            logging.info(f"Attempting to send message: '{message}' on channel {channel_index}")
            mesh_packet = interface.sendText(message, channelIndex=channel_index, wantAck=True)
            packet_id = mesh_packet.id
            logging.info(f"Sent message: '{message}' on channel {channel_index} with packet ID: {packet_id}")
            
            # Send to Discord webhook
            try:
                with open(WEBHOOK_FILE, 'r') as f:
                    webhook_data = json.load(f)
                    webhook_url = webhook_data.get('url')
                
                if webhook_url:
                    my_node_info = interface.getMyNodeInfo()
                    device_info = my_node_info.get('user', {}).get('hwModel', 'Unknown Device')
                    battery_level = my_node_info.get('deviceMetrics', {}).get('batteryLevel')
                    if battery_level is not None:
                        device_info += f" (🔋 {battery_level}%)"

                    embed = {
                        "title": "📡 New Meshtastic Message",
                        "description": message,
                        "color": 3447003,
                        "fields": [
                            {"name": "👤 From", "value": my_node_info.get('user', {}).get('longName', 'You')},
                            {"name": "🔧 Device", "value": device_info}
                        ],
                        "footer": {"text": "Meshtastic"},
                        "timestamp": datetime.utcnow().isoformat()
                    }

                    position = my_node_info.get('position', {})
                    if position.get('latitude') is not None and position.get('longitude') is not None:
                        embed["fields"].append({
                            "name": "📍 Location",
                            "value": f"[View on Map](https://www.google.com/maps?q={position['latitude']},{position['longitude']})"
                        })

                    webhook_payload = {"embeds": [embed]}
                    response = requests.post(webhook_url, json=webhook_payload)
                    response.raise_for_status()
                    logging.info("Message sent to Discord webhook successfully")
                else:
                    logging.info("No Discord webhook URL set")
            except Exception as e:
                logging.error(f"Error sending to Discord webhook: {e}")

            socketio.emit('message_sent', {
                'status': 'success',
                'packetId': packet_id,
                'message': message,
                'channel': channel_index,
                'timestamp': int(time.time())
            })
            
            socketio.start_background_task(wait_for_ack, interface, packet_id)
        else:
            logging.error("Serial interface not connected")
            socketio.emit('serial_error', {'message': 'Not connected'})
    except Exception as e:
        logging.error(f"Error in send_message: {str(e)}")
        logging.exception("Stack trace:")
        socketio.emit('serial_error', {'message': str(e)})
        
@socketio.on('load_webhook_url')
def handle_load_webhook_url():
    try:
        if os.path.exists(WEBHOOK_FILE):
            with open(WEBHOOK_FILE, 'r') as f:
                data = json.load(f)
                url = data.get('url', '')
                logging.info(f"Loaded Discord webhook URL: {'Set' if url else 'Not set'}")
                socketio.emit('webhook_url_loaded', {'url': url})
        else:
            logging.info("Discord webhook file does not exist")
            socketio.emit('webhook_url_loaded', {'url': ''})
    except Exception as e:
        logging.error(f"Error loading webhook URL: {e}")
        socketio.emit('webhook_url_loaded', {'url': ''})

@socketio.on('save_webhook_url')
def handle_save_webhook_url(data):
    try:
        url = data.get('url', '')
        with open(WEBHOOK_FILE, 'w') as f:
            json.dump({'url': url}, f)
        logging.info(f"Saved Discord webhook URL: {'Set' if url else 'Not set'}")
        socketio.emit('webhook_url_saved', {'url': url})
    except Exception as e:
        logging.error(f"Error saving webhook URL: {e}")

@socketio.on('delete_webhook_url')
def handle_delete_webhook_url():
    try:
        if os.path.exists(WEBHOOK_FILE):
            os.remove(WEBHOOK_FILE)
            logging.info("Deleted Discord webhook URL")
        else:
            logging.info("No Discord webhook URL to delete")
        socketio.emit('webhook_url_deleted')
    except Exception as e:
        logging.error(f"Error deleting webhook URL: {e}")

ack_events = {}

def wait_for_ack(interface, packet_id):
    ack_event = Event()
    ack_events[packet_id] = ack_event
    try:
        if ack_event.wait(timeout=60):
            logging.info(f"ACK received for packet ID: {packet_id}")
            socketio.emit('message_ack', {'packetId': packet_id, 'status': 'success'})
        else:
            logging.warning(f"ACK timeout for packet ID {packet_id}")
            socketio.emit('message_ack_timeout', {'packetId': packet_id, 'status': 'timeout'})
    except Exception as e:
        logging.error(f"Error waiting for ACK for packet ID {packet_id}: {str(e)}")
    finally:
        del ack_events[packet_id]

def handle_ack_message(decoded_data, packet):
    try:
        ack_packet_id = decoded_data.get('requestId')
        if ack_packet_id:
            logging.info(f"ACK received for packet ID: {ack_packet_id}")
            socketio.emit('message_ack', {'packetId': ack_packet_id, 'status': 'success'})
            
            for channel, channel_messages in app.config['messages'].items():
                for message in channel_messages:
                    if message.get('packetId') == ack_packet_id:
                        message['status'] = 'acked'
                        break
            
            if ack_packet_id in ack_events:
                ack_events[ack_packet_id].set()
            
            socketio.emit('update_message_status', {'packetId': ack_packet_id, 'status': 'acked'})
        else:
            logging.warning(f"Received ACK message without requestId: {decoded_data}")
    except Exception as e:
        logging.error(f"Error in handle_ack_message: {e}")
        logging.exception("Stack trace:")

if __name__ == '__main__':
    socketio.run(app, port=5678)

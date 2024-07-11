function hasValidPosition(node) {
    return node.position &&
        typeof node.position.latitude === 'number' &&
        typeof node.position.longitude === 'number' &&
        !isNaN(node.position.latitude) &&
        !isNaN(node.position.longitude);
}

$(document).ready(function() {
    var socket = io();
    var currentChannel = 0;
    var nodes = {};
    var messages = {};
    var map;
    var markers = {};
    var messageTimeouts = {};
    var discordWebhook = '';
    var myNodeInfo = null;
    var footer = $('.footer-buttons');
    var lastScrollTop = 0;
    var delta = 5;
    var footerHeight = footer.outerHeight();

    function openSettingsPage() {
        window.location.href = '/settings';
    }

    $('#settingsBtn').click(function() {
        openSettingsPage();
    });

    function populateSettingsForm(settings) {
        $('#deviceName').val(settings.deviceName);
        $('#serialEnabled').prop('checked', settings.serialEnabled);
        $('#nodeInfoBroadcastSecs').val(settings.nodeInfoBroadcastSecs);
        $('#positionBroadcastSecs').val(settings.positionBroadcastSecs);
        $('#fixedPosition').prop('checked', settings.fixedPosition);
        $('#gpsUpdateInterval').val(settings.gpsUpdateInterval);
        $('#positionFlags').val(settings.positionFlags);
        $('#latitude').val(settings.latitude);
        $('#longitude').val(settings.longitude);
        $('#altitude').val(settings.altitude);
        $('#region').val(settings.region);
        $('#txPower').val(settings.txPower);
        $('#txEnabled').prop('checked', settings.txEnabled);
        $('#waitBluetoothSecs').val(settings.waitBluetoothSecs);
        $('#sdsSecs').val(settings.sdsSecs);
        $('#lsSecs').val(settings.lsSecs);
        $('#minWakeSecs').val(settings.minWakeSecs);
        $('#ntpServer').val(settings.ntpServer);
        $('#ethEnabled').prop('checked', settings.ethEnabled);
        $('#screenOnSecs').val(settings.screenOnSecs);
        $('#bluetoothEnabled').prop('checked', settings.bluetoothEnabled);
        $('#fixedPin').val(settings.fixedPin);
        $('#mqttEnabled').prop('checked', settings.mqttEnabled);
        $('#mqttAddress').val(settings.mqttAddress);
        $('#mqttUsername').val(settings.mqttUsername);
        $('#mqttPassword').val(settings.mqttPassword);
        $('#mqttEncryptionEnabled').prop('checked', settings.mqttEncryptionEnabled);
    }
    
    let storedSettings = {};

    socket.on('settings_data', function(settings) {
        console.log('Received settings data:', settings);
        storedSettings = settings;
        populateSettingsForm(settings);
    });
    
    function populateSettingsForm(settings) {
        Object.keys(settings).forEach(key => {
            const element = document.getElementById(key);
            if (element) {
                if (element.type === 'checkbox') {
                    element.checked = settings[key];
                } else {
                    element.value = settings[key];
                }
            }
        });
    }

    function initializeData() {
        try {
            if (typeof initialChannels !== 'undefined' && initialChannels.length > 0) {
                updateChannelList(initialChannels);
            } else {
                logDebugMessage('No initial channel data available');
                updateChannelList([{index: 0, name: 'Default Channel'}]);
            }

            if (typeof initialNodes !== 'undefined' && Object.keys(initialNodes).length > 0) {
                nodes = initialNodes;
                updateNodeInfo();
                logDebugMessage('Nodes populated: ' + Object.keys(nodes).length);
            } else {
                logDebugMessage('No initial node data available');
            }

            messages = typeof initialMessages !== 'undefined' ? initialMessages : {};
            updateMessages();

            if (Object.keys(messages).length === 0) {
                logDebugMessage('No initial message data available');
            }
        } catch (error) {
            console.error('Error in initial data population:', error);
            logDebugMessage('Error populating initial data: ' + error.message);
        }
    }

    initializeData();
    initMap();        

    $('#debugBtn').click(function() {
        $('#debugContainer').toggle();
        $(this).toggleClass('btn-secondary btn-primary');
        if ($('#debugContainer').is(':visible')) {
            $('html, body').animate({
                scrollTop: $('#debugContainer').offset().top - 20
            }, 500);
        }
    });

    $('#settingsBtn').click(function() {
        console.log('Settings button clicked');
        logDebugMessage('Settings button clicked');
    });
    
    $(window).scroll(function(event) {
            var st = $(this).scrollTop();

            if (Math.abs(lastScrollTop - st) <= delta)
                return;

            if (st > lastScrollTop && st > footerHeight){
                footer.addClass('hidden');
            } else {
                if(st + $(window).height() < $(document).height()) {
                    footer.removeClass('hidden');
                }
            }

            if ($(window).scrollTop() + $(window).height() > $(document).height() - 10) {
                footer.removeClass('hidden');
            }

            lastScrollTop = st;
        });

    function initMap() {
        map = L.map('map').setView([0, 0], 2);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors'
        }).addTo(map);
    }

    $('#discordWebhookBtn').click(function() {
            $('#discordWebhookModal').modal('show');
            loadWebhookUrl();
        });

        $('#saveWebhook').click(function() {
            var url = $('#webhookUrl').val().trim();
            if (url) {
                saveWebhookUrl(url);
                $('#discordWebhookModal').modal('hide');
            } else {
                alert('Please enter a valid webhook URL.');
            }
        });

        $('#deleteWebhook').click(function() {
            deleteWebhookUrl();
            $('#webhookUrl').val('');
        });

        function loadWebhookUrl() {
            socket.emit('load_webhook_url');
            logDebugMessage('Requesting Discord webhook URL');
        }

        function saveWebhookUrl(url) {
            socket.emit('save_webhook_url', { url: url });
            logDebugMessage('Saving Discord webhook URL: ' + url);
        }

        function deleteWebhookUrl() {
            socket.emit('delete_webhook_url');
            logDebugMessage('Deleting Discord webhook URL');
        }

    function updateMap() {
        var bounds = L.latLngBounds();
        var hasValidPositionNode = false;

        Object.values(nodes).forEach(function(node) {
            if (hasValidPosition(node)) {
                var latLng = L.latLng(node.position.latitude, node.position.longitude);
                bounds.extend(latLng);
                hasValidPositionNode = true;

                if (markers[node.num]) {
                    markers[node.num].setLatLng(latLng);
                } else {
                    var nodeName = ((node.user && node.user.longName) || '') + 
                                   ((node.user && node.user.shortName) ? ` (${node.user.shortName})` : '');
                    if (!nodeName.trim()) nodeName = node.num || 'Unknown';

                    markers[node.num] = L.marker(latLng).addTo(map)
                        .bindPopup(nodeName);
                }
            } else if (markers[node.num]) {
                map.removeLayer(markers[node.num]);
                delete markers[node.num];
            }
        });

        if (hasValidPositionNode) {
            map.fitBounds(bounds, { padding: [50, 50] });
        }
    }

function formatUptime(seconds) {
    if (seconds === undefined) return 'N/A';
    var days = Math.floor(seconds / 86400);
    var hours = Math.floor((seconds % 86400) / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    var remainingSeconds = seconds % 60;
    return `${days}d ${hours}h ${minutes}m ${remainingSeconds}s`;
}    

function getMyNodeName() {
        return myNodeInfo ? `${myNodeInfo.user.longName} (${myNodeInfo.user.shortName})` : 'My Node';
    }

function updateMyNodeInfo(nodeInfo) {
        myNodeInfo = nodeInfo;
        logDebugMessage('My node info updated: ' + JSON.stringify(myNodeInfo));
    }

$('#nodeSortSelect').change(function() {
    updateNodeInfo();
});

$('#nodeFilterInput').on('input', function() {
    updateNodeInfo();
});

function updateMyNodeInfo(nodeInfo) {
    myNodeInfo = nodeInfo;
    logDebugMessage('My node info updated: ' + JSON.stringify(myNodeInfo));
}

function updateNode(nodeData) {
    var nodeId = nodeData.num;
    if (!nodes[nodeId]) {
        nodes[nodeId] = {};
    }
    nodes[nodeId] = Object.assign({}, nodes[nodeId], nodeData);
    updateNodeInfo();
    updateMap();
}

function updateNodeInfo() {
    var nodeCount = Object.keys(nodes).length;
    $('#nodeCount').text(`(${nodeCount})`);
    
    var nodeInfoContainer = $('#nodeInfo');
    nodeInfoContainer.empty();
    
    var sortBy = $('#nodeSortSelect').val();
    var filterText = $('#nodeFilterInput').val().toLowerCase();
    
    var filteredNodes = Object.values(nodes).filter(function(node) {
        var nodeName = ((node.user && node.user.longName) || '') + 
                       ((node.user && node.user.shortName) ? ` (${node.user.shortName})` : '');
        var nodeId = node.num ? node.num.toString().toLowerCase() : '';
        return nodeName.toLowerCase().includes(filterText) || nodeId.includes(filterText);
    });
    
    filteredNodes.sort(function(a, b) {
        if (sortBy === 'lastHeard') {
            return (b.lastHeard || 0) - (a.lastHeard || 0);
        } else if (sortBy === 'snr') {
            return (b.snr || -Infinity) - (a.snr || -Infinity);
        } else if (sortBy === 'hopsAway') {
            return (a.hopsAway || Infinity) - (b.hopsAway || Infinity);
        }
        return 0;
    });

    filteredNodes.forEach(function(node) {
        var nodeName = ((node.user && node.user.longName) || '') + 
                       ((node.user && node.user.shortName) ? ` (${node.user.shortName})` : '');
        if (!nodeName.trim()) nodeName = node.num || 'Unknown';
        
        var nodeHtml = `
            <div class="card mb-2">
                <div class="card-body">
                    <h5 class="card-title">${nodeName}</h5>
                    <p class="card-text">
                        <strong>ID:</strong> ${node.num || 'N/A'}<br>
                        <strong>Last Heard:</strong> ${node.lastHeard ? new Date(node.lastHeard * 1000).toLocaleString() : 'N/A'}<br>
                        <strong>SNR:</strong> ${node.snr !== undefined ? node.snr.toFixed(2) : 'N/A'}<br>
                        <strong>Hops Away:</strong> ${node.hopsAway !== undefined ? node.hopsAway : 'N/A'}<br>
                        <strong>Via MQTT:</strong> ${node.viaMqtt ? 'Yes' : 'No'}<br>
                        ${node.user ? `
                            <strong>User ID:</strong> ${node.user.id || 'N/A'}<br>
                            <strong>Mac Address:</strong> ${node.user.macaddr || 'N/A'}<br>
                            <strong>Hardware Model:</strong> ${node.user.hwModel || 'N/A'}<br>
                            <strong>Role:</strong> ${node.user.role || 'N/A'}<br>
                            <strong>Licensed:</strong> ${node.user.isLicensed ? 'Yes' : 'No'}<br>
                        ` : ''}
                    </p>
                    ${node.position ? `
                        <p>
                            <strong>Position:</strong><br>
                            Latitude: ${node.position.latitude !== undefined ? node.position.latitude.toFixed(7) : 'N/A'}<br>
                            Longitude: ${node.position.longitude !== undefined ? node.position.longitude.toFixed(7) : 'N/A'}<br>
                            Altitude: ${node.position.altitude !== undefined ? node.position.altitude + 'm' : 'N/A'}<br>
                            Time: ${node.position.time ? new Date(node.position.time * 1000).toLocaleString() : 'N/A'}<br>
                            ${node.position.PDOP !== undefined ? `PDOP: ${node.position.PDOP}<br>` : ''}
                            ${node.position.groundSpeed !== undefined ? `Ground Speed: ${node.position.groundSpeed} m/s<br>` : ''}
                            ${node.position.groundTrack !== undefined ? `Ground Track: ${(node.position.groundTrack / 1000000).toFixed(6)}°<br>` : ''}
                            ${node.position.satsInView !== undefined ? `Satellites in View: ${node.position.satsInView}<br>` : ''}
                            ${node.position.precisionBits !== undefined ? `Precision Bits: ${node.position.precisionBits}<br>` : ''}
                        </p>
                    ` : ''}
                    ${node.deviceMetrics ? `
                        <p>
                            <strong>Device Metrics:</strong><br>
                            ${Object.entries(node.deviceMetrics).map(([key, value]) => {
                                if (key === 'batteryLevel') value += '%';
                                else if (key === 'voltage') value = value.toFixed(3) + 'V';
                                else if (key === 'uptimeSeconds') value = formatUptime(value);
                                return `${key}: ${value}<br>`;
                            }).join('')}
                        </p>
                    ` : ''}
                    ${node.telemetry && node.telemetry.deviceMetrics ? `
                        <p>
                            <strong>Telemetry:</strong><br>
                            Time: ${node.telemetry.time ? new Date(node.telemetry.time * 1000).toLocaleString() : 'N/A'}<br>
                            ${Object.entries(node.telemetry.deviceMetrics).map(([key, value]) => {
                                if (key === 'batteryLevel') value += '%';
                                else if (key === 'voltage') value = value.toFixed(3) + 'V';
                                else if (key === 'uptimeSeconds') value = formatUptime(value);
                                return `${key}: ${value}<br>`;
                            }).join('')}
                        </p>
                    ` : ''}
                </div>
            </div>
        `;
        nodeInfoContainer.append(nodeHtml);
    });

    updateMap();
    logDebugMessage('Node information updated');
}

$('#nodeSortSelect').change(updateNodeInfo);
$('#nodeFilterInput').on('input', updateNodeInfo);

function updateMessages() {
    var messagesContainer = $('#messagesContainer');
    messagesContainer.empty();

    console.log("Current channel:", currentChannel);
    console.log("All messages:", messages);

    if (!messages || !messages[currentChannel] || !Array.isArray(messages[currentChannel])) {
        console.log("No messages for the current channel");
        return;
    }

    messages[currentChannel].forEach(function(msg) {
        if (!msg || !msg.text) {
            console.log("Invalid message:", msg);
            return;  
        }
        
        var messageText;
        var senderName = msg.sender === 'You' ? getMyNodeName() : msg.sender;
        var statusDot = msg.sender === 'You' ? 
            (msg.status === 'pending' ? '🟡' : (msg.status === 'acked' ? '🟢' : '🔴')) : '🟢';
        
        messageText = `${statusDot} <strong>${senderName}:</strong><br>${msg.text}<br><small>${new Date(msg.timestamp * 1000).toLocaleString()} (ID: ${msg.packetId})</small>`;
        
        messagesContainer.append('<p>' + messageText + '</p><hr>');
    });

    messagesContainer.scrollTop(messagesContainer[0].scrollHeight);
    console.log('Messages updated for channel ' + currentChannel);
}

function updateChannelList(channels) {
    var channelSelect = $('#channelSelect');
    channelSelect.empty();
    channels.forEach(function(channel) {
        if (channel.index === 0 || channel.name) {
            channelSelect.append($('<option>', {
                value: channel.index,
                text: channel.index === 0 ? 'PRIMARY' : channel.name
            }));
        }
    });
    logDebugMessage('Channel list updated');
}

$('#connectBtn').click(function() {
    var selectedPort = $('#portSelect').val();
    if (!selectedPort) {
        logDebugMessage('Error: No port selected');
        alert('Please select a port before connecting.');
        return;
    }
    socket.emit('connect_serial', { port: selectedPort });
    logDebugMessage('Attempting to connect to port: ' + selectedPort);
});

$('#disconnectBtn').click(function() {
    socket.emit('disconnect_serial');
    logDebugMessage('Attempting to disconnect from serial port');
});

$('#messageInput').keypress(function(e) {
    if (e.which == 13) { 
        e.preventDefault(); 
        $('#sendBtn').click(); 
    }
});

$('#sendBtn').click(function() {
    var message = $('#messageInput').val().trim();
    if (message) {
        socket.emit('send_message', { message: message, channel: currentChannel });
        logDebugMessage('Sending message: "' + message + '" on channel ' + currentChannel);
        $('#messageInput').val('');
    } else {
        logDebugMessage('Error: Empty message');
        alert('Please enter a message before sending.');
    }
});

$('#channelSelect').change(function() {
    currentChannel = parseInt($(this).val());
    updateMessages();
    logDebugMessage('Switched to channel: ' + currentChannel);
});

socket.on('connect', function() {
    socket.emit('get_settings');
    logDebugMessage('Socket.IO connected to server');
});

socket.on('disconnect', function() {
    logDebugMessage('Socket.IO disconnected from server');
});

socket.on('serial_connected', function(data) {
    logDebugMessage('Serial connected: ' + JSON.stringify(data));
    $('#connectBtn').prop('disabled', true).removeClass('btn-primary').addClass('btn-secondary');
    $('#disconnectBtn').prop('disabled', false).removeClass('btn-secondary').addClass('btn-danger');
    $('#sendBtn').prop('disabled', false).removeClass('btn-secondary').addClass('btn-success');

    if (data.initialNodeInfo) {
        updateMyNodeInfo(data.initialNodeInfo);
        updateNode(data.initialNodeInfo);
    }
});

socket.on('serial_disconnected', function() {
    logDebugMessage('Serial disconnected');
    $('#connectBtn').prop('disabled', false).removeClass('btn-secondary').addClass('btn-primary');
    $('#disconnectBtn').prop('disabled', true).removeClass('btn-danger').addClass('btn-secondary');
    $('#sendBtn').prop('disabled', true).removeClass('btn-success').addClass('btn-secondary');
    $('#channelSelect').empty();
    $('#messagesContainer').empty();
    $('#nodeInfo').empty();
    nodes = {};
    messages = {};
});

socket.on('serial_error', function(data) {
    logDebugMessage('Serial error: ' + JSON.stringify(data));
    alert('Serial error: ' + data.message);
});

socket.on('routing_error', function(data) {
    logDebugMessage('Routing error received: ' + JSON.stringify(data));
    updateMessageStatus(data.packetId, 'failed', data.error);
});

socket.on('settings_data', function(settings) {
    populateSettingsForm(settings);
});

socket.on('update_message_status', function(data) {
    logDebugMessage('Message status update received: ' + JSON.stringify(data));
    updateMessageStatus(data.packetId, data.status, data.error);
});

socket.on('channel_list', function(data) {
    logDebugMessage('Channel list received: ' + JSON.stringify(data));
    updateChannelList(data.channels);
});

socket.on('new_message', function(data) {
    console.log("New message received:", data);
    var messageData = data.raw_message || data;
    if (!messageData || typeof messageData.channel === 'undefined') {
        console.error('Error: Invalid message format');
        return;
    }
    if (!messages[messageData.channel]) {
        messages[messageData.channel] = [];
    }
    
    var existingMessageIndex = messages[messageData.channel].findIndex(function(msg) {
        return msg.packetId === messageData.packetId;
    });
    
    if (existingMessageIndex !== -1) {
        messages[messageData.channel][existingMessageIndex] = messageData;
    } else {
        messages[messageData.channel].push(messageData);
    }
    
    if (messageData.channel === currentChannel) {
        updateMessages();
        console.log("Sending message to Discord webhook:", messageData);
        sendToDiscordWebhook(messageData);
    }
});

socket.on('message_sent', function(data) {
    if (data.status === 'success') {
        logDebugMessage('Message sent successfully');
        var sentMessage = {
            sender: 'You',
            text: data.message, 
            channel: data.channel,
            timestamp: data.timestamp || Math.floor(Date.now() / 1000),
            packetId: data.packetId,
            status: 'pending'
        };
        if (!messages[data.channel]) {
            messages[data.channel] = [];
        }
        messages[data.channel].push(sentMessage);
        updateMessages();
        
        messageTimeouts[data.packetId] = setTimeout(function() {
            updateMessageStatus(data.packetId, 'timeout');
        }, 60000);
    } else {
        logDebugMessage('Error sending message: ' + data.message);
    }
});

socket.on('node_updated', function(data) {
        logDebugMessage('Node updated: ' + JSON.stringify(data));
        if (!data.num && !(data.user && data.user.id)) {
            logDebugMessage('Error: Invalid node data');
            return;
        }
        var nodeId = data.num || (data.user && data.user.id);
        if (!nodes[nodeId]) {
            nodes[nodeId] = {};
        }
        nodes[nodeId] = Object.assign({}, nodes[nodeId], data);
        
        if (data.isLocal) {
            updateMyNodeInfo(data);
        }
        
        updateNodeInfo();
        updateMap();
    });

socket.on('message_ack', function(data) {
    logDebugMessage('ACK received for packet ID: ' + data.packetId);
    if (messageTimeouts[data.packetId]) {
        clearTimeout(messageTimeouts[data.packetId]);
        delete messageTimeouts[data.packetId];
    }
    updateMessageStatus(data.packetId, 'acked');
});

socket.on('message_ack_timeout', function(data) {
    logDebugMessage('ACK timeout for packet ID: ' + data.packetId);
    if (messageTimeouts[data.packetId]) {
        updateMessageStatus(data.packetId, 'timeout');
        delete messageTimeouts[data.packetId];
    }
});

$(document).ready(function() {
    $('#settingsForm').on('submit', function(event) {
        event.preventDefault();
        
        var settings = {
            deviceName: $('#deviceName').val(),
            serialEnabled: $('#serialEnabled').is(':checked'),
            nodeInfoBroadcastSecs: parseInt($('#nodeInfoBroadcastSecs').val()),
            positionBroadcastSecs: parseInt($('#positionBroadcastSecs').val()),
            fixedPosition: $('#fixedPosition').is(':checked'),
            gpsUpdateInterval: parseInt($('#gpsUpdateInterval').val()),
            positionFlags: parseInt($('#positionFlags').val()),
            broadcastSmartMinimumDistance: parseInt($('#broadcastSmartMinimumDistance').val()),
            broadcastSmartMinimumIntervalSecs: parseInt($('#broadcastSmartMinimumIntervalSecs').val()),
            gpsMode: $('#gpsMode').val(),
            waitBluetoothSecs: parseInt($('#waitBluetoothSecs').val()),
            sdsSecs: parseInt($('#sdsSecs').val()),
            lsSecs: parseInt($('#lsSecs').val()),
            minWakeSecs: parseInt($('#minWakeSecs').val()),
            ntpServer: $('#ntpServer').val(),
            ethEnabled: $('#ethEnabled').is(':checked'),
            screenOnSecs: parseInt($('#screenOnSecs').val()),
            usePreset: $('#usePreset').is(':checked'),
            region: $('#region').val(),
            hopLimit: parseInt($('#hopLimit').val()),
            txEnabled: $('#txEnabled').is(':checked'),
            txPower: parseInt($('#txPower').val()),
            sx126xRxBoostedGain: $('#sx126xRxBoostedGain').is(':checked'),
            bluetoothEnabled: $('#bluetoothEnabled').is(':checked'),
            fixedPin: parseInt($('#fixedPin').val()),
            mqttEnabled: $('#mqttEnabled').is(':checked'),
            mqttAddress: $('#mqttAddress').val(),
            mqttUsername: $('#mqttUsername').val(),
            mqttPassword: $('#mqttPassword').val(),
            mqttEncryptionEnabled: $('#mqttEncryptionEnabled').is(':checked'),
            mqttRoot: $('#mqttRoot').val(),
            mqttProxyToClientEnabled: $('#mqttProxyToClientEnabled').is(':checked'),
            mqttMapReportingEnabled: $('#mqttMapReportingEnabled').is(':checked'),
            mqttPositionPrecision: parseInt($('#mqttPositionPrecision').val()),
            telemetryDeviceUpdateInterval: parseInt($('#telemetryDeviceUpdateInterval').val()),
            telemetryEnvironmentUpdateInterval: parseInt($('#telemetryEnvironmentUpdateInterval').val()),
            telemetryAirQualityInterval: parseInt($('#telemetryAirQualityInterval').val()),
            neighborInfoEnabled: $('#neighborInfoEnabled').is(':checked'),
            neighborInfoUpdateInterval: parseInt($('#neighborInfoUpdateInterval').val()),
            ambientLightingCurrent: parseInt($('#ambientLightingCurrent').val()),
            ambientLightingRed: parseInt($('#ambientLightingRed').val()),
            ambientLightingGreen: parseInt($('#ambientLightingGreen').val()),
            ambientLightingBlue: parseInt($('#ambientLightingBlue').val()),
            detectionSensorMinimumBroadcastSecs: parseInt($('#detectionSensorMinimumBroadcastSecs').val()),
            detectionSensorDetectionTriggeredHigh: $('#detectionSensorDetectionTriggeredHigh').is(':checked'),
            latitude: parseFloat($('#latitude').val()),
            longitude: parseFloat($('#longitude').val()),
            altitude: parseInt($('#altitude').val())
        };

        Object.keys(settings).forEach(key => 
            (settings[key] === undefined || Number.isNaN(settings[key])) && delete settings[key]
        );

        socket.emit('save_settings', settings);

        $('#saveStatus').text('Saving settings and rebooting...')
                        .removeClass('text-success text-danger')
                        .addClass('text-info');
    });

    socket.on('save_success', function() {
        $('#saveStatus').text('Settings saved successfully! Rebooting device...')
                        .removeClass('text-info text-danger')
                        .addClass('text-success');
    });

    socket.on('save_error', function(error) {
        $('#saveStatus').text('Error saving settings: ' + error)
                        .removeClass('text-info text-success')
                        .addClass('text-danger');
    });
});

$('[data-toggle="tooltip"]').tooltip();

socket.on('webhook_url_loaded', function(data) {
    discordWebhook = data.url;
    $('#webhookUrl').val(discordWebhook);
    $('#deleteWebhook').toggle(!!discordWebhook);
    updateSendButtonTooltip();
    console.log('Discord webhook URL loaded:', discordWebhook ? 'URL set' : 'No URL');
});

socket.on('webhook_url_saved', function(data) {
    discordWebhook = data.url;
    updateSendButtonTooltip();
    logDebugMessage('Discord webhook URL saved: ' + discordWebhook);
});

socket.on('webhook_url_deleted', function() {
    discordWebhook = '';
    updateSendButtonTooltip();
    logDebugMessage('Discord webhook URL deleted');
});

function updateSendButtonTooltip() {
    var $sendBtn = $('#sendBtn');
    if (discordWebhook) {
        $sendBtn.attr('title', 'Discord Webhook enabled');
    } else {
        $sendBtn.attr('title', 'Send');
    }
    $sendBtn.tooltip('dispose').tooltip();
}

function updateMessageStatus(packetId, status) {
    Object.keys(messages).forEach(function(channel) {
        messages[channel].forEach(function(msg) {
            if (msg.packetId === packetId) {
                msg.status = status;
                logDebugMessage('Message status updated to ' + status + ' for packet ID: ' + packetId);
            }
        });
    });
    updateMessages();
}

    function requestAllMessages() {
        socket.emit('get_messages');
    }

    socket.on('all_messages', function(data) {
        console.log("All messages received:", data);
        messages = data.messages || {};
        updateMessages();
    });

    $('#channelSelect').change(function() {
        currentChannel = parseInt($(this).val());
        updateMessages();
        console.log('Switched to channel: ' + currentChannel);
    });

    socket.emit('get_messages');
});


function logDebugMessage(message) {
    var debugWindow = $('#debugWindow');
    var timestamp = new Date().toLocaleString();
    debugWindow.append('<p>[' + timestamp + '] ' + message + '</p>');
    debugWindow.scrollTop(debugWindow[0].scrollHeight);
}

# Meshtastic Web Chat Interface

The Meshtastic Chat Interface is a web-based application that allows you to communicate with Meshtastic devices using a user-friendly interface. With this application, you can select a COM port, connect to a Meshtastic device, view available channels, send messages, and monitor the serial communication.

## Prerequisites

Before running the application, ensure that you have the following:

- Python installed on your system
- Meshtastic device connected to your computer

## Running the Application

1. Start the Flask server by running the `app.py` script:

`python app.py`

The server will start running on `http://127.0.0.1:5678`.

2. Open your web browser and visit `http://127.0.0.1:5678` to access the Meshtastic Chat Interface.

3. The application will prompt you to select a COM port. Choose the appropriate port from the dropdown list and click "Connect".

4. Once connected, you will see the available channels on the left sidebar. Click on a channel to select it.

5. In the chat area, you can type your message in the input field and click "Send Message" to send it to the selected channel.

6. The messages received on the selected channel will be displayed in the messages window.

7. The serial monitor at the bottom of the page will display the raw data received from the Meshtastic device.

8. To clear the serial monitor, click the "Clear Serial Monitor" button.

## Troubleshooting

- If you encounter any issues connecting to the Meshtastic device, ensure that the device is properly connected to your computer and the correct COM port is selected.

- If you experience any other problems or have questions, please open an issue on the GitHub repository.

## Contributing

Contributions to the Meshtastic Chat Interface are welcome! If you find any bugs, have feature requests, or want to contribute improvements, please open an issue or submit a pull request on the GitHub repository.

## License

[LICENSE](LICENSE)


![rocketgod_logo](https://github.com/RocketGod-git/shodanbot/assets/57732082/7929b554-0fba-4c2b-b22d-6772d23c4a18)

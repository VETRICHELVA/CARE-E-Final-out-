# cold-box firmware

Firmware for the cold-chain box: an ESP32 reads a DS18B20 temperature probe and publishes telemetry over MQTT to the Mosquitto broker, where `services/iot-ingest` picks it up. Includes a telemetry simulator for demos without hardware. Built in S14.

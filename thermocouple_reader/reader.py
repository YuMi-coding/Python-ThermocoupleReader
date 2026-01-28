import serial
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)


class ThermocoupleReader:

    def __init__(self, port="/dev/ttyUSB0", baudrate=9600, reconnect_delay=1):
        self.port = port
        self.baudrate = baudrate
        self.serial = None
        self.reconnect_delay = reconnect_delay

    def open(self):
        """Open the serial connection."""
        try:
            if not self.serial or not self.serial.is_open:
                self.serial = serial.Serial(self.port, self.baudrate, timeout=1)
                logging.info(f"Serial port {self.port} opened.")
        except serial.SerialException as e:
            logging.error(f"Failed to open serial port {self.port}: {e}")
            self.serial = None

    def close(self):
        """Close the serial connection."""
        if self.serial and self.serial.is_open:
            self.serial.close()
            logging.info(f"Serial port {self.port} closed.")

    def __del__(self):
        """Ensure the serial connection is closed when the object is destroyed."""
        self.close()

    def read_temperatures(self):
        """Read temperature values from up to 4 channels."""
        if not self.serial or not self.serial.is_open:
            logging.warning("Serial port is not open. Trying to reconnect...")
            self.open()
            return None

        try:
            command = bytes([0xAA, 0x55, 0x01, 0x03, 0x03])
            self.serial.write(command)
            response = self.serial.read(64)

            start = response.find(b"\x55\xaa")
            if start == -1 or len(response) < start + 13:
                logging.warning(f"No valid response. Raw: {response.hex()}")
                return None

            temp_data = response[start + 4 : start + 4 + 8]
            temperatures = []

            for i in range(0, len(temp_data), 2):
                raw_bytes = temp_data[i : i + 2]
                raw = int.from_bytes(raw_bytes, byteorder="little", signed=True)
                temperatures.append(raw / 10.0 if raw != 28000 else None)

            return temperatures

        except serial.SerialException as e:
            logging.error(f"Serial error: {e}")
            self.close()
            logging.info(f"Reconnecting in {self.reconnect_delay} seconds...")
            time.sleep(self.reconnect_delay)
            self.open()
            return None


# Example usage
if __name__ == "__main__":
    reader = ThermocoupleReader("/dev/ttyUSB0")
    reader.open()

    try:
        for i in range(100):
            temps = reader.read_temperatures()
            if temps:
                ch1, ch2, ch3, ch4 = temps
                logging.info(
                    f"Channel 1: {ch1}°C, Channel 2: {ch2}°C, Channel 3: {ch3}°C, Channel 4: {ch4}°C"
                )
            else:
                logging.warning("No data (device not ready?)")
            time.sleep(1)

    except KeyboardInterrupt:
        logging.info("Interrupted by user.")

    finally:
        reader.close()

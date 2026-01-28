import csv
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

from serial.tools import list_ports

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
)

import pyqtgraph as pg

from thermocouple_reader.reader import ThermocoupleReader


def iso_now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def available_ports() -> List[str]:
    ports = [p.device for p in list_ports.comports()]
    return ports

def default_csv_name(test_id: int) -> str:
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"thermocouple_{ts}_test{test_id}.csv"

@dataclass
class Sample:
    ts: str
    ch1: Optional[float]
    ch2: Optional[float]
    ch3: Optional[float]
    ch4: Optional[float]


class ReaderWorker(QObject):
    sample = Signal(object)          # emits Sample
    status = Signal(str)
    error = Signal(str)
    finished = Signal()

    def __init__(self, port: str, baud: int, interval_s: float):
        super().__init__()
        self.port = port
        self.baud = baud
        self.interval_s = interval_s
        self._stop = False
        self._reader = ThermocoupleReader(port=self.port, baudrate=self.baud)

    @Slot()
    def run(self):
        try:
            self._reader.open()
            if not self._reader.serial or not getattr(self._reader.serial, "is_open", False):
                self.error.emit(f"Failed to open {self.port}. Is it in use?")
                self.finished.emit()
                return

            self.status.emit(f"Running: {self.port}, {self.baud} baud, interval={self.interval_s}s")
            next_t = time.monotonic()

            while not self._stop:
                now = time.monotonic()
                sleep_s = next_t - now
                if sleep_s > 0:
                    time.sleep(sleep_s)
                next_t += self.interval_s

                temps = self._reader.read_temperatures()
                ts = iso_now_local()

                if temps and len(temps) >= 4:
                    ch1, ch2, ch3, ch4 = temps[:4]
                else:
                    ch1 = ch2 = ch3 = ch4 = None

                self.sample.emit(Sample(ts, ch1, ch2, ch3, ch4))

        except Exception as e:
            self.error.emit(str(e))
        finally:
            try:
                self._reader.close()
            except Exception:
                pass
            self.status.emit("Stopped.")
            self.finished.emit()

    def stop(self):
        self._stop = True


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Thermocouple Reader (Qt)")
        self.resize(1100, 700)

        self.launch_ts = datetime.now()
        self.default_csv = default_csv_name(1)
        self.test_index = 1

        self.thread: Optional[QThread] = None
        self.worker: Optional[ReaderWorker] = None

        self.csv_file = None
        self.csv_writer = None

        # Plot buffers
        self.max_points = 3000
        self.x = []
        self.y = [[], [], [], []]
        self.idx = 0

        self._build_ui()
        self.refresh_ports()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)

        # --- Controls ---
        form = QFormLayout()
        layout.addLayout(form)

        row1 = QHBoxLayout()
        self.port_combo = QComboBox()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_ports)
        row1.addWidget(self.port_combo)
        row1.addWidget(self.refresh_btn)
        form.addRow("COM Port:", row1)

        self.baud_combo = QComboBox()
        for b in ["9600", "19200", "38400", "57600", "115200"]:
            self.baud_combo.addItem(b)
        form.addRow("Baudrate:", self.baud_combo)

        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.1, 3600.0)
        self.interval_spin.setSingleStep(0.5)
        self.interval_spin.setValue(5.0)
        form.addRow("Interval (s):", self.interval_spin)

        path_row = QHBoxLayout()
        self.csv_path = QLineEdit()
        self.csv_path.setText(self.default_csv)
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.clicked.connect(self.browse_csv)
        self.append_chk = QCheckBox("Append")
        path_row.addWidget(self.csv_path)
        path_row.addWidget(self.browse_btn)
        path_row.addWidget(self.append_chk)
        form.addRow("CSV Path:", path_row)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)

        self.status_lbl = QLabel("Idle.")
        btn_row.addWidget(self.status_lbl)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.latest_lbl = QLabel("Latest: (no data yet)")
        layout.addWidget(self.latest_lbl)

        # --- Plot ---
        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "Temperature", units="°C")
        self.plot.setLabel("bottom", "Sample #")
        self.plot.addLegend()

        self.curves = []
        for i in range(4):
            c = self.plot.plot([], [], name=f"CH{i+1}")
            self.curves.append(c)

        layout.addWidget(self.plot, stretch=1)

    @Slot()
    def refresh_ports(self):
        current = self.port_combo.currentText()
        self.port_combo.clear()
        ports = available_ports()

        if not ports:
            ports = ["COM3"]  # fallback

        self.port_combo.addItems(ports)
        if current and current in ports:
            self.port_combo.setCurrentText(current)

    @Slot()
    def browse_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Select CSV output",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )
        if path:
            if not path.lower().endswith(".csv"):
                path += ".csv"
            self.csv_path.setText(path)

    def _open_csv(self, path: str, append: bool):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        exists = os.path.exists(path)
        mode = "a" if (append and exists) else "w"
        f = open(path, mode, newline="", encoding="utf-8")
        w = csv.writer(f)
        if not (append and exists):
            w.writerow(["timestamp_local", "ch1_c", "ch2_c", "ch3_c", "ch4_c"])
            f.flush()
        return f, w

    @Slot()
    def start(self):
        if self.thread is not None:
            return

        port = self.port_combo.currentText().strip()
        if not port:
            QMessageBox.critical(self, "Error", "Please select a COM port.")
            return

        baud = int(self.baud_combo.currentText())
        interval = float(self.interval_spin.value())

        out = self.csv_path.text().strip()
        if not out:
            QMessageBox.critical(self, "Error", "Please choose a CSV output path.")
            return

        try:
            self.csv_file, self.csv_writer = self._open_csv(out, self.append_chk.isChecked())
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open CSV:\n{e}")
            return

        # reset plot buffers
        self.idx = 0
        self.x.clear()
        for i in range(4):
            self.y[i].clear()
            self.curves[i].setData([], [])

        self.thread = QThread()
        self.worker = ReaderWorker(port, baud, interval)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.sample.connect(self.on_sample)
        self.worker.status.connect(self.status_lbl.setText)
        self.worker.error.connect(self.on_error)
        self.worker.finished.connect(self.on_finished)

        self.thread.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    @Slot()
    def stop(self):
        if self.worker is not None:
            self.worker.stop()
        self.stop_btn.setEnabled(False)

    @Slot(object)
    def on_sample(self, s: Sample):
        # CSV
        if self.csv_writer:
            self.csv_writer.writerow([s.ts, s.ch1, s.ch2, s.ch3, s.ch4])
            if self.csv_file:
                self.csv_file.flush()

        # UI labels
        self.latest_lbl.setText(
            f"Latest [{s.ts}]: CH1={s.ch1}  CH2={s.ch2}  CH3={s.ch3}  CH4={s.ch4}"
        )

        # plot buffers
        self.x.append(self.idx)
        self.idx += 1
        vals = [s.ch1, s.ch2, s.ch3, s.ch4]
        for i in range(4):
            self.y[i].append(vals[i] if vals[i] is not None else float("nan"))

        # trim
        if len(self.x) > self.max_points:
            drop = len(self.x) - self.max_points
            del self.x[:drop]
            for i in range(4):
                del self.y[i][:drop]

        # update curves
        for i in range(4):
            self.curves[i].setData(self.x, self.y[i])

    @Slot(str)
    def on_error(self, msg: str):
        QMessageBox.critical(self, "Reader Error", msg)

    @Slot()
    def on_finished(self):
        # tear down thread
        try:
            if self.thread:
                self.thread.quit()
                self.thread.wait(2000)
        except Exception:
            pass
        self.thread = None
        self.worker = None

        # close CSV
        try:
            if self.csv_file:
                self.csv_file.flush()
                self.csv_file.close()
        except Exception:
            pass
        self.csv_file = None
        self.csv_writer = None

        self.test_index += 1
        self.default_csv = default_csv_name(self.test_index)
        self.csv_path.setText(self.default_csv)

        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def closeEvent(self, event):
        # stop worker cleanly
        if self.worker is not None:
            self.worker.stop()
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait(2000)
        self.on_finished()
        event.accept()


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

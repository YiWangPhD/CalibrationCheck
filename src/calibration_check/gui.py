"""PySide6 desktop GUI for CalibrationCheck."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QUrl

from .config import convert_network_ini, load_config
from .models import COMBINABLE_QUANTITIES, NETWORK_ELEMENT_TYPES


GREEN_BUTTON_STYLE = (
    "QPushButton[softGreen='true'] {"
    "min-width: 92px;"
    "padding: 7px 12px;"
    "color: #102418;"
    "border: 1px solid #6ba875;"
    "border-radius: 4px;"
    "background: #bfe8c4;"
    "}"
    "QPushButton[softGreen='true']:hover { background: #a9dcaf; }"
    "QPushButton[softGreen='true']:pressed { background: #8fcb98; }"
    "QPushButton[softGreen='true']:disabled {"
    "color: #68746b;"
    "border-color: #9ba79e;"
    "background: #d7ded8;"
    "}"
)

APP_STYLE = (
    "QLabel { font-size: 12px; }"
    "QLineEdit, QPlainTextEdit, QComboBox {"
    "border: 1px solid #b8bec8;"
    "border-radius: 4px;"
    "padding: 6px;"
    "}"
    "QTableWidget { border: 1px solid #b8bec8; }"
    "#statusLabel { color: #344054; }"
    + GREEN_BUTTON_STYLE
)

RESULT_FILE_EXTENSIONS = {".res1d", ".res", ".resx", ".whr"}
ELEMENT_TYPES = sorted(NETWORK_ELEMENT_TYPES)
QUANTITIES = sorted(COMBINABLE_QUANTITIES | {"WaterLevel", "Discharge"})


def mark_green_button(button: QPushButton) -> QPushButton:
    button.setProperty("softGreen", True)
    button.setStyleSheet(GREEN_BUTTON_STYLE)
    return button


def empty_config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project": {"name": "Calibration Check", "output_folder": "output"},
        "result_type": "network",
        "results": [],
        "monitoring_sources": {},
        "gauges": [],
        "report": {
            "title": "Calibration Check",
            "output_file": "calibration_report.html",
            "self_contained": True,
        },
    }


def normalize_config(data: dict[str, Any] | None) -> dict[str, Any]:
    config = empty_config()
    if isinstance(data, dict):
        config.update(copy.deepcopy(data))
    config["schema_version"] = 1
    config["result_type"] = "network"
    config["project"] = {**empty_config()["project"], **(config.get("project") or {})}
    config["report"] = {**empty_config()["report"], **(config.get("report") or {})}
    config["results"] = list(config.get("results") or [])
    config["monitoring_sources"] = dict(config.get("monitoring_sources") or {})
    config["gauges"] = list(config.get("gauges") or [])
    return config


def save_config_to_json(config: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2, allow_nan=False), encoding="utf-8")
    return target


def timestamped_json_path(source_path: str | None) -> Path:
    timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    if source_path:
        source = Path(source_path)
        return source.with_name(f"{source.stem}_{timestamp}.json")
    return Path.cwd() / f"calibration_config_{timestamp}.json"


def text_timestamp(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return pd.Timestamp(value).isoformat()
    except (TypeError, ValueError):
        return str(value)


def hours_between(start: Any, end: Any) -> str:
    try:
        delta = pd.Timestamp(end) - pd.Timestamp(start)
    except (TypeError, ValueError):
        return ""
    hours = delta.total_seconds() / 3600
    return f"{hours:g}"


def timestamp_plus_hours(value: Any, hours: str) -> str:
    try:
        return (pd.Timestamp(value) + pd.Timedelta(hours=float(hours))).isoformat()
    except (TypeError, ValueError):
        return text_timestamp(value)


def timestamp_minus_hours(value: Any, hours: str) -> str:
    try:
        return (pd.Timestamp(value) - pd.Timedelta(hours=float(hours))).isoformat()
    except (TypeError, ValueError):
        return text_timestamp(value)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CalibrationCheck")
        self.resize(920, 640)
        self.process: QProcess | None = None
        self.current_config: dict[str, Any] | None = None
        self.config_source_path: str | None = None
        self.editor: ConfigEditorWindow | None = None
        self.last_report_path: Path | None = None

        self.input_edit = QLineEdit()
        self.status_label = QLabel("No configuration loaded")
        self.status_label.setObjectName("statusLabel")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log.setFont(QFont("Consolas", 10))

        self.run_button = mark_green_button(QPushButton("Run"))
        self.cancel_button = mark_green_button(QPushButton("Cancel"))
        self.cancel_button.setEnabled(False)
        self.open_report_button = mark_green_button(QPushButton("Open Report"))
        self.open_report_button.setEnabled(False)

        self._build_layout()

    def _build_layout(self) -> None:
        root = QWidget()
        root.setStyleSheet(APP_STYLE)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        form = QGridLayout()
        form.setColumnStretch(1, 1)
        form.addWidget(QLabel("Configuration file"), 0, 0)
        form.addWidget(self.input_edit, 0, 1)
        browse = mark_green_button(QPushButton("Browse"))
        browse.clicked.connect(self.browse_input)
        form.addWidget(browse, 0, 2)
        form.addWidget(QLabel("Status"), 1, 0)
        form.addWidget(self.status_label, 1, 1, 1, 2)
        layout.addLayout(form)

        actions = QHBoxLayout()
        validate = mark_green_button(QPushButton("Validate"))
        validate.clicked.connect(self.validate_config)
        editor = mark_green_button(QPushButton("Open Configuration"))
        editor.clicked.connect(self.open_editor)
        save_as = mark_green_button(QPushButton("Save As JSON"))
        save_as.clicked.connect(self.save_as_json)
        clear_log = mark_green_button(QPushButton("Clear Log"))
        clear_log.clicked.connect(self.log.clear)
        self.run_button.clicked.connect(self.run_calibration)
        self.cancel_button.clicked.connect(self.cancel_run)
        self.open_report_button.clicked.connect(self.open_report)

        for button in [
            self.run_button,
            self.cancel_button,
            validate,
            editor,
            save_as,
            self.open_report_button,
        ]:
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(clear_log)
        layout.addLayout(actions)

        self.log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.log)
        self.setCentralWidget(root)

    def browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select calibration configuration",
            os.getcwd(),
            "Calibration files (*.json *.ini);;JSON files (*.json);;INI files (*.ini)",
        )
        if path:
            self.load_input(path)

    def load_input(self, path: str) -> None:
        try:
            if Path(path).suffix.lower() == ".ini":
                conversion = convert_network_ini(path)
                self.current_config = normalize_config(conversion.config)
                self.append_log(f"Loaded legacy INI: {path}")
                for warning in conversion.warnings:
                    self.append_log(f"Warning: {warning}")
            else:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("Configuration root must be a JSON object.")
                self.current_config = normalize_config(data)
                self.append_log(f"Loaded JSON: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Load Failed", str(exc))
            return
        self.config_source_path = path
        self.input_edit.setText(path)
        self._set_status("Configuration loaded", ok=True)

    def open_editor(self) -> None:
        if self.editor is not None:
            self.editor.raise_()
            self.editor.activateWindow()
            return
        if self.current_config is None:
            input_path = self.input_edit.text().strip()
            if input_path and Path(input_path).is_file():
                self.load_input(input_path)
            else:
                self.current_config = empty_config()
                self.config_source_path = None
        self.editor = ConfigEditorWindow(self.current_config, self.config_source_path)
        self.editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.editor.config_changed.connect(self.receive_config_from_editor)
        self.editor.destroyed.connect(lambda _obj=None: self._editor_closed())
        self.editor.show()

    def receive_config_from_editor(self, config: dict, source_path: object) -> None:
        self.current_config = normalize_config(config)
        self.config_source_path = str(source_path) if source_path else None
        if self.config_source_path:
            self.input_edit.setText(f"Using edited configuration from {self.config_source_path}")
        else:
            self.input_edit.setText("Using edited in-memory configuration")
        self._set_status("Edited configuration ready", ok=True)

    def _editor_closed(self) -> None:
        self.editor = None

    def save_as_json(self) -> None:
        if self.current_config is None:
            self.open_editor()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save calibration JSON",
            str(Path.cwd() / "calibration.json"),
            "JSON files (*.json)",
        )
        if not path:
            return
        saved = save_config_to_json(normalize_config(self.current_config), path)
        self.config_source_path = str(saved)
        self.input_edit.setText(str(saved))
        self._set_status(f"Saved {saved}", ok=True)

    def validate_config(self) -> bool:
        path = self._materialize_config_for_validation()
        if path is None:
            return False
        try:
            config = load_config(path)
        except Exception as exc:
            QMessageBox.warning(self, "Configuration Issues", str(exc))
            self._set_status("Configuration has issues", ok=False)
            return False
        self._set_status(
            f"Valid: {len(config.gauges)} gauge(s), {len(config.results)} result file(s)",
            ok=True,
        )
        return True

    def run_calibration(self) -> None:
        run_path = self._materialize_config_for_validation()
        if run_path is None:
            return
        try:
            load_config(run_path)
        except Exception as exc:
            QMessageBox.warning(self, "Configuration Issues", str(exc))
            self._set_status("Run blocked by configuration issues", ok=False)
            return
        self._start_process(run_path)

    def _materialize_config_for_validation(self) -> Path | None:
        if self.current_config is not None:
            source = self.config_source_path
            path = timestamped_json_path(source)
            save_config_to_json(normalize_config(self.current_config), path)
            return path
        input_path = self.input_edit.text().strip()
        if not input_path:
            QMessageBox.warning(self, "Input Required", "Select a JSON or INI file first.")
            return None
        if not Path(input_path).is_file():
            QMessageBox.warning(self, "Input Not Found", "The selected file does not exist.")
            return None
        return Path(input_path)

    def _start_process(self, config_path: Path) -> None:
        if self.process is not None:
            return
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_process_output)
        self.process.finished.connect(self._process_finished)
        args = ["-m", "calibration_check", "run", str(config_path)]
        self.append_log("")
        self.append_log(f"> {sys.executable} {' '.join(args)}")
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.open_report_button.setEnabled(False)
        self.last_report_path = None
        self.process.start(sys.executable, args)
        if not self.process.waitForStarted(5000):
            self.append_log("Failed to start process.")
            self._reset_process_buttons()
            self.process = None

    def _read_process_output(self) -> None:
        if self.process is None:
            return
        output = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        if output:
            self.append_log(output.rstrip())
            for line in output.splitlines():
                if line.startswith("Report written to:"):
                    self.last_report_path = Path(line.split(":", 1)[1].strip())

    def _process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        if self.process is not None:
            self._read_process_output()
        self.append_log(f"Process finished with exit code {exit_code}.")
        self._reset_process_buttons()
        self.process = None
        if exit_code == 0:
            self._set_status("Run complete", ok=True)
            self.open_report_button.setEnabled(self.last_report_path is not None)
        else:
            self._set_status("Run failed", ok=False)

    def cancel_run(self) -> None:
        if self.process is None:
            return
        self.append_log("Cancel requested.")
        self.process.terminate()
        if not self.process.waitForFinished(5000):
            self.process.kill()

    def open_report(self) -> None:
        if self.last_report_path is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_report_path.resolve())))

    def _reset_process_buttons(self) -> None:
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _set_status(self, text: str, ok: bool) -> None:
        self.status_label.setStyleSheet(f"color: {'#087443' if ok else '#b42318'};")
        self.status_label.setText(text)

    def append_log(self, text: str) -> None:
        self.log.appendPlainText(text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())


class ConfigEditorWindow(QWidget):
    config_changed = Signal(dict, object)

    def __init__(self, config: dict[str, Any] | None, source_path: str | None) -> None:
        super().__init__()
        self.setWindowTitle("Calibration Configuration")
        self.resize(1180, 780)
        self.config = normalize_config(config)
        self.source_path = source_path
        self.tabs_by_name: dict[str, QWidget] = {}
        self._build_layout()
        self._emit_change()

    def _build_layout(self) -> None:
        self.setStyleSheet(APP_STYLE)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        load = mark_green_button(QPushButton("Load JSON/INI"))
        load.clicked.connect(self.load_file)
        save = mark_green_button(QPushButton("Save JSON"))
        save.clicked.connect(self.save_json)
        clear = mark_green_button(QPushButton("Clear All"))
        clear.clicked.connect(self.clear_all)
        close = mark_green_button(QPushButton("Close"))
        close.clicked.connect(self.close)
        toolbar.addWidget(load)
        toolbar.addWidget(save)
        toolbar.addWidget(clear)
        toolbar.addStretch(1)
        toolbar.addWidget(close)
        layout.addLayout(toolbar)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._create_tabs()

    def _create_tabs(self) -> None:
        self.tabs.clear()
        self.tabs_by_name.clear()
        project = ProjectTab(self.config)
        project.changed.connect(self._tab_changed)
        self.tabs.addTab(project, "Project")
        self.tabs_by_name["project"] = project

        results = ResultsTab(self.config.get("results", []))
        results.changed.connect(self._tab_changed)
        self.tabs.addTab(results, "res1d Files")
        self.tabs_by_name["results"] = results

        monitoring = MonitoringTab(self.config.get("monitoring_sources", {}))
        monitoring.changed.connect(self._tab_changed)
        self.tabs.addTab(monitoring, "Monitoring")
        self.tabs_by_name["monitoring"] = monitoring

        gauges = GaugesTab(self.config)
        gauges.changed.connect(self._tab_changed)
        self.tabs.addTab(gauges, "Gauges")
        self.tabs_by_name["gauges"] = gauges

        options = OptionsTab(self.config)
        options.changed.connect(self._tab_changed)
        self.tabs.addTab(options, "Options")
        self.tabs_by_name["options"] = options

    def load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load calibration configuration",
            str(Path.cwd()),
            "Calibration files (*.json *.ini);;JSON files (*.json);;INI files (*.ini)",
        )
        if not path:
            return
        try:
            if Path(path).suffix.lower() == ".ini":
                self.config = normalize_config(convert_network_ini(path).config)
            else:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                self.config = normalize_config(data)
        except Exception as exc:
            QMessageBox.critical(self, "Load Failed", str(exc))
            return
        self.source_path = path
        self._create_tabs()
        self._emit_change()

    def save_json(self) -> None:
        self._pull_config_from_tabs()
        default_name = "calibration.json"
        if self.source_path:
            default_name = f"{Path(self.source_path).stem}.json"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save calibration JSON",
            str(Path.cwd() / default_name),
            "JSON files (*.json)",
        )
        if not path:
            return
        saved = save_config_to_json(self.config, path)
        self.source_path = str(saved)
        self._emit_change()

    def clear_all(self) -> None:
        self.config = empty_config()
        self.source_path = None
        self._create_tabs()
        self._emit_change()

    def _tab_changed(self) -> None:
        self._pull_config_from_tabs()
        gauges = self.tabs_by_name.get("gauges")
        if isinstance(gauges, GaugesTab):
            gauges.update_context(self.config)
        self._emit_change()

    def _pull_config_from_tabs(self) -> None:
        project = self.tabs_by_name.get("project")
        if isinstance(project, ProjectTab):
            project.apply_to(self.config)
        results = self.tabs_by_name.get("results")
        if isinstance(results, ResultsTab):
            self.config["results"] = results.data()
        monitoring = self.tabs_by_name.get("monitoring")
        if isinstance(monitoring, MonitoringTab):
            self.config["monitoring_sources"] = monitoring.data()
        gauges = self.tabs_by_name.get("gauges")
        if isinstance(gauges, GaugesTab):
            self.config["gauges"] = gauges.data()
        options = self.tabs_by_name.get("options")
        if isinstance(options, OptionsTab):
            options.apply_to(self.config)
        self.config = normalize_config(self.config)

    def _emit_change(self) -> None:
        self.config_changed.emit(copy.deepcopy(self.config), self.source_path)


class ProjectTab(QWidget):
    changed = Signal()

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        project = config.get("project") or {}
        layout = QFormLayout(self)
        self.name_edit = QLineEdit(str(project.get("name") or "Calibration Check"))
        self.output_edit = QLineEdit(str(project.get("output_folder") or "output"))
        browse = mark_green_button(QPushButton("Browse"))
        browse.clicked.connect(self.pick_folder)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit)
        output_row.addWidget(browse)
        layout.addRow("project name", self.name_edit)
        layout.addRow("output folder", output_row)
        layout.addRow("result type", QLabel("network"))
        self.name_edit.textChanged.connect(self.changed.emit)
        self.output_edit.textChanged.connect(self.changed.emit)

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select output folder", self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    def apply_to(self, config: dict[str, Any]) -> None:
        config["project"] = {
            "name": self.name_edit.text(),
            "output_folder": self.output_edit.text(),
        }
        config["result_type"] = "network"


class OptionsTab(QWidget):
    changed = Signal()

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        report = config.get("report") or {}
        layout = QFormLayout(self)
        self.title_edit = QLineEdit(str(report.get("title") or "Calibration Check"))
        self.output_file_edit = QLineEdit(str(report.get("output_file") or "calibration_report.html"))
        self.self_contained = QCheckBox()
        self.self_contained.setChecked(bool(report.get("self_contained", True)))
        layout.addRow("report title", self.title_edit)
        layout.addRow("report output file", self.output_file_edit)
        layout.addRow("self-contained HTML", self.self_contained)
        self.title_edit.textChanged.connect(self.changed.emit)
        self.output_file_edit.textChanged.connect(self.changed.emit)
        self.self_contained.stateChanged.connect(self.changed.emit)

    def apply_to(self, config: dict[str, Any]) -> None:
        config["report"] = {
            "title": self.title_edit.text(),
            "output_file": self.output_file_edit.text(),
            "self_contained": self.self_contained.isChecked(),
        }


class ResultsTab(QWidget):
    changed = Signal()

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__()
        self.columns = ["name", "path", "start_time", "end_time"]
        self.rows = copy.deepcopy(rows)
        self.selected_row = -1
        self.inputs: dict[str, QLineEdit] = {}
        self._updating = False
        self._last_folder = Path.cwd()
        self._build_layout()
        self.refresh_table()

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        for column in self.columns:
            edit = QLineEdit()
            edit.editingFinished.connect(self._form_editing_finished)
            self.inputs[column] = edit
            form.addRow(column, edit)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        for label, slot in [
            ("Add Row", self.add_row),
            ("Copy Row", self.copy_row),
            ("Delete Row", self.delete_row),
            ("Pick File", self.pick_file),
            ("Pick Folder", self.pick_folder),
        ]:
            button = mark_green_button(QPushButton(label))
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.table = make_table(self.columns)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table)

    def data(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self.rows)

    def add_row(self) -> None:
        self.rows.append({"name": "", "path": "", "start_time": "", "end_time": ""})
        self.selected_row = len(self.rows) - 1
        self.refresh_table()
        self._fill_form(self.rows[self.selected_row])
        self.changed.emit()

    def copy_row(self) -> None:
        if self.selected_row < 0:
            return
        self.rows.insert(self.selected_row + 1, copy.deepcopy(self.rows[self.selected_row]))
        self.selected_row += 1
        self.refresh_table()
        self.changed.emit()

    def delete_row(self) -> None:
        if self.selected_row < 0:
            return
        del self.rows[self.selected_row]
        self.selected_row = -1
        self.refresh_table()
        self._fill_form({})
        self.changed.emit()

    def pick_file(self) -> None:
        if self.selected_row < 0:
            self.add_row()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select result file",
            str(self._last_folder),
            "Result files (*.res1d *.res *.resx *.whr);;All files (*.*)",
        )
        if path:
            result_path = Path(path)
            self._last_folder = result_path.parent
            row = self.rows[self.selected_row]
            row["path"] = path
            if not row.get("name"):
                row["name"] = result_path.stem
            self.refresh_table()
            self._fill_form(row)
            self.changed.emit()

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select result file folder", str(self._last_folder))
        if not folder:
            return
        folder_path = Path(folder)
        self._last_folder = folder_path
        result_files = sorted(
            path for path in folder_path.iterdir()
            if path.is_file() and path.suffix.lower() in RESULT_FILE_EXTENSIONS
        )
        for path in result_files:
            self.rows.append({
                "name": path.stem,
                "path": str(path),
                "start_time": "",
                "end_time": "",
            })
        if result_files:
            self.selected_row = len(self.rows) - 1
            self.refresh_table()
            self._fill_form(self.rows[self.selected_row])
            self.changed.emit()

    def refresh_table(self) -> None:
        self._updating = True
        self.table.setRowCount(len(self.rows))
        for row_index, row in enumerate(self.rows):
            for column_index, column in enumerate(self.columns):
                self.table.setItem(row_index, column_index, QTableWidgetItem(str(row.get(column) or "")))
        if 0 <= self.selected_row < len(self.rows):
            self.table.selectRow(self.selected_row)
        self._updating = False

    def _selection_changed(self) -> None:
        if self._updating:
            return
        indexes = self.table.selectionModel().selectedRows()
        self.selected_row = indexes[0].row() if indexes else -1
        self._fill_form(self.rows[self.selected_row] if self.selected_row >= 0 else {})

    def _fill_form(self, row: dict[str, Any]) -> None:
        self._updating = True
        for column, edit in self.inputs.items():
            edit.setText(str(row.get(column) or ""))
        self._updating = False

    def _form_editing_finished(self) -> None:
        if self._updating or self.selected_row < 0:
            return
        for column, edit in self.inputs.items():
            self.rows[self.selected_row][column] = edit.text()
        self.refresh_table()
        self.changed.emit()


class MonitoringTab(QWidget):
    changed = Signal()

    def __init__(self, sources: dict[str, Any]) -> None:
        super().__init__()
        self.columns = ["alias", "format", "path"]
        self.rows = [
            {"alias": alias, "format": value.get("format", "dfs0"), "path": value.get("path", "")}
            for alias, value in sources.items()
        ]
        self.selected_row = -1
        self.inputs: dict[str, QLineEdit] = {}
        self._updating = False
        self._build_layout()
        self.refresh_table()

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        for column in self.columns:
            edit = QLineEdit()
            edit.editingFinished.connect(self._form_editing_finished)
            if column == "format":
                edit.setText("dfs0")
                edit.setEnabled(False)
            self.inputs[column] = edit
            form.addRow(column, edit)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        for label, slot in [
            ("Add Row", self.add_row),
            ("Copy Row", self.copy_row),
            ("Delete Row", self.delete_row),
            ("Pick DFS0", self.pick_file),
        ]:
            button = mark_green_button(QPushButton(label))
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.table = make_table(self.columns)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table)

    def data(self) -> dict[str, dict[str, str]]:
        return {
            str(row.get("alias") or ""): {
                "format": "dfs0",
                "path": str(row.get("path") or ""),
            }
            for row in self.rows
            if row.get("alias")
        }

    def add_row(self) -> None:
        self.rows.append({"alias": "", "format": "dfs0", "path": ""})
        self.selected_row = len(self.rows) - 1
        self.refresh_table()
        self._fill_form(self.rows[self.selected_row])
        self.changed.emit()

    def copy_row(self) -> None:
        if self.selected_row < 0:
            return
        self.rows.insert(self.selected_row + 1, copy.deepcopy(self.rows[self.selected_row]))
        self.selected_row += 1
        self.refresh_table()
        self.changed.emit()

    def delete_row(self) -> None:
        if self.selected_row < 0:
            return
        del self.rows[self.selected_row]
        self.selected_row = -1
        self.refresh_table()
        self._fill_form({})
        self.changed.emit()

    def pick_file(self) -> None:
        if self.selected_row < 0:
            self.add_row()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select DFS0 monitoring file",
            str(Path.cwd()),
            "DFS0 files (*.dfs0);;All files (*.*)",
        )
        if path:
            row = self.rows[self.selected_row]
            row["path"] = path
            if not row.get("alias"):
                row["alias"] = Path(path).stem
            self.refresh_table()
            self._fill_form(row)
            self.changed.emit()

    def refresh_table(self) -> None:
        self._updating = True
        self.table.setRowCount(len(self.rows))
        for row_index, row in enumerate(self.rows):
            for column_index, column in enumerate(self.columns):
                self.table.setItem(row_index, column_index, QTableWidgetItem(str(row.get(column) or "")))
        if 0 <= self.selected_row < len(self.rows):
            self.table.selectRow(self.selected_row)
        self._updating = False

    def _selection_changed(self) -> None:
        if self._updating:
            return
        indexes = self.table.selectionModel().selectedRows()
        self.selected_row = indexes[0].row() if indexes else -1
        self._fill_form(self.rows[self.selected_row] if self.selected_row >= 0 else {})

    def _fill_form(self, row: dict[str, Any]) -> None:
        self._updating = True
        for column, edit in self.inputs.items():
            edit.setText(str(row.get(column) or ("dfs0" if column == "format" else "")))
        self._updating = False

    def _form_editing_finished(self) -> None:
        if self._updating or self.selected_row < 0:
            return
        for column, edit in self.inputs.items():
            self.rows[self.selected_row][column] = "dfs0" if column == "format" else edit.text()
        self.refresh_table()
        self.changed.emit()


class GaugesTab(QWidget):
    changed = Signal()

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        self.config = normalize_config(config)
        self.gauges = copy.deepcopy(self.config.get("gauges", []))
        self.selected_gauge = -1
        self._updating = False
        self._build_layout()
        self.refresh_gauge_table()

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        editor = QGridLayout()
        editor.addWidget(QLabel("Gauge alias"), 0, 0)
        self.alias_edit = QLineEdit()
        self.alias_edit.editingFinished.connect(self._alias_finished)
        editor.addWidget(self.alias_edit, 0, 1)
        layout.addLayout(editor)

        signal_bar = QHBoxLayout()
        signal_bar.addWidget(QLabel("Signals"))
        signal_bar.addStretch(1)
        for label, slot in [
            ("Add Signal", self.add_signal),
            ("Edit Signal", self.edit_signal),
            ("Delete Signal", self.delete_signal),
        ]:
            button = mark_green_button(QPushButton(label))
            button.clicked.connect(slot)
            signal_bar.addWidget(button)
        layout.addLayout(signal_bar)
        self.signals_table = make_table(["alias", "observed", "simulated"])
        self.signals_table.setMaximumHeight(170)
        layout.addWidget(self.signals_table)

        event_bar = QHBoxLayout()
        event_bar.addWidget(QLabel("Events"))
        event_bar.addStretch(1)
        sync = mark_green_button(QPushButton("Sync Events From Result Files"))
        sync.clicked.connect(self.sync_events)
        event_bar.addWidget(sync)
        layout.addLayout(event_bar)
        self.events_table = make_table([
            "Use",
            "Event name",
            "Result file",
            "Result start",
            "Result end",
            "Event start",
            "Event end",
            "Skip hours",
            "Truncate hours",
        ])
        self.events_table.setMaximumHeight(230)
        self.events_table.itemChanged.connect(self._event_item_changed)
        layout.addWidget(self.events_table)

        gauge_bar = QHBoxLayout()
        for label, slot in [
            ("Add Gauge", self.add_gauge),
            ("Copy Gauge", self.copy_gauge),
            ("Delete Gauge", self.delete_gauge),
        ]:
            button = mark_green_button(QPushButton(label))
            button.clicked.connect(slot)
            gauge_bar.addWidget(button)
        gauge_bar.addStretch(1)
        layout.addLayout(gauge_bar)
        self.gauge_table = make_table(["alias", "signals", "events used", "result files used"])
        self.gauge_table.itemSelectionChanged.connect(self._gauge_selection_changed)
        layout.addWidget(self.gauge_table)

    def update_context(self, config: dict[str, Any]) -> None:
        self.config = normalize_config(config)
        self.gauges = copy.deepcopy(self.config.get("gauges", self.gauges))
        self.refresh_gauge_table()
        self.refresh_editor()

    def data(self) -> list[dict[str, Any]]:
        self._pull_current_gauge()
        return copy.deepcopy(self.gauges)

    def add_gauge(self) -> None:
        self.gauges.append({
            "alias": self._next_gauge_alias(),
            "signals": [],
            "events": self._default_events(),
        })
        self.selected_gauge = len(self.gauges) - 1
        self.refresh_gauge_table()
        self.refresh_editor()
        self.changed.emit()

    def copy_gauge(self) -> None:
        if self.selected_gauge < 0:
            return
        self._pull_current_gauge()
        item = copy.deepcopy(self.gauges[self.selected_gauge])
        item["alias"] = f"{item.get('alias', 'Gauge')} copy"
        self.gauges.insert(self.selected_gauge + 1, item)
        self.selected_gauge += 1
        self.refresh_gauge_table()
        self.refresh_editor()
        self.changed.emit()

    def delete_gauge(self) -> None:
        if self.selected_gauge < 0:
            return
        del self.gauges[self.selected_gauge]
        self.selected_gauge = -1
        self.refresh_gauge_table()
        self.refresh_editor()
        self.changed.emit()

    def add_signal(self) -> None:
        if self.selected_gauge < 0:
            self.add_gauge()
        gauge = self.gauges[self.selected_gauge]
        dialog = SignalDialog({}, self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            gauge.setdefault("signals", []).append(dialog.data())
            self.refresh_editor()
            self.refresh_gauge_table()
            self.changed.emit()

    def edit_signal(self) -> None:
        if self.selected_gauge < 0:
            return
        row = self.signals_table.currentRow()
        signals = self.gauges[self.selected_gauge].setdefault("signals", [])
        if row < 0 or row >= len(signals):
            return
        dialog = SignalDialog(signals[row], self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            signals[row] = dialog.data()
            self.refresh_editor()
            self.refresh_gauge_table()
            self.changed.emit()

    def delete_signal(self) -> None:
        if self.selected_gauge < 0:
            return
        row = self.signals_table.currentRow()
        signals = self.gauges[self.selected_gauge].setdefault("signals", [])
        if 0 <= row < len(signals):
            del signals[row]
            self.refresh_editor()
            self.refresh_gauge_table()
            self.changed.emit()

    def sync_events(self) -> None:
        if self.selected_gauge < 0:
            return
        self._pull_current_gauge()
        events = self.gauges[self.selected_gauge].setdefault("events", [])
        existing = {str(event.get("name") or "") for event in events}
        for result in self.config.get("results", []):
            name = str(result.get("name") or "").strip()
            if name and name not in existing:
                events.append({
                    "name": name,
                    "start": text_timestamp(result.get("start_time")),
                    "end": text_timestamp(result.get("end_time")),
                })
        self.refresh_editor()
        self.refresh_gauge_table()
        self.changed.emit()

    def refresh_gauge_table(self) -> None:
        self._updating = True
        self.gauge_table.setRowCount(len(self.gauges))
        result_names = {
            str(result.get("name") or "")
            for result in self.config.get("results", [])
        }
        for row_index, gauge in enumerate(self.gauges):
            events = gauge.get("events") or []
            used_results = sorted({
                str(event.get("name") or "")
                for event in events
                if str(event.get("name") or "") in result_names
            })
            values = [
                str(gauge.get("alias") or ""),
                str(len(gauge.get("signals") or [])),
                str(len(events)),
                ", ".join(used_results),
            ]
            for column_index, value in enumerate(values):
                self.gauge_table.setItem(row_index, column_index, QTableWidgetItem(value))
        if 0 <= self.selected_gauge < len(self.gauges):
            self.gauge_table.selectRow(self.selected_gauge)
        self._updating = False

    def refresh_editor(self) -> None:
        self._updating = True
        if self.selected_gauge < 0 or self.selected_gauge >= len(self.gauges):
            self.alias_edit.clear()
            self.signals_table.setRowCount(0)
            self.events_table.setRowCount(0)
            self._updating = False
            return
        gauge = self.gauges[self.selected_gauge]
        self.alias_edit.setText(str(gauge.get("alias") or ""))
        self._refresh_signals(gauge)
        self._refresh_events(gauge)
        self._updating = False

    def _refresh_signals(self, gauge: dict[str, Any]) -> None:
        signals = gauge.get("signals") or []
        self.signals_table.setRowCount(len(signals))
        for row_index, signal in enumerate(signals):
            values = [
                str(signal.get("alias") or ""),
                "yes" if signal.get("observed") else "",
                "combined" if (signal.get("simulated") or {}).get("terms") else ("yes" if signal.get("simulated") else ""),
            ]
            for column_index, value in enumerate(values):
                self.signals_table.setItem(row_index, column_index, QTableWidgetItem(value))

    def _refresh_events(self, gauge: dict[str, Any]) -> None:
        events_by_name = {
            str(event.get("name") or ""): event
            for event in gauge.get("events") or []
        }
        rows: list[dict[str, Any]] = []
        for result in self.config.get("results", []):
            name = str(result.get("name") or "")
            event = events_by_name.get(name)
            rows.append({
                "use": event is not None,
                "event_name": name,
                "result_name": name,
                "result_start": result.get("start_time", ""),
                "result_end": result.get("end_time", ""),
                "event_start": (event or {}).get("start", result.get("start_time", "")),
                "event_end": (event or {}).get("end", result.get("end_time", "")),
            })
        result_names = {str(result.get("name") or "") for result in self.config.get("results", [])}
        for event in gauge.get("events") or []:
            name = str(event.get("name") or "")
            if name not in result_names:
                rows.append({
                    "use": True,
                    "event_name": name,
                    "result_name": "(custom/orphan)",
                    "result_start": "",
                    "result_end": "",
                    "event_start": event.get("start", ""),
                    "event_end": event.get("end", ""),
                })
        self._event_rows = rows
        self.events_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            checkbox = QTableWidgetItem("")
            checkbox.setFlags(checkbox.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checkbox.setCheckState(Qt.CheckState.Checked if row["use"] else Qt.CheckState.Unchecked)
            self.events_table.setItem(row_index, 0, checkbox)
            values = [
                row["event_name"],
                row["result_name"],
                text_timestamp(row["result_start"]),
                text_timestamp(row["result_end"]),
                text_timestamp(row["event_start"]),
                text_timestamp(row["event_end"]),
                hours_between(row["result_start"], row["event_start"]),
                hours_between(row["event_end"], row["result_end"]),
            ]
            for offset, value in enumerate(values, start=1):
                item = QTableWidgetItem(str(value or ""))
                if offset in {2, 3}:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.events_table.setItem(row_index, offset, item)

    def _event_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating or self.selected_gauge < 0:
            return
        row = item.row()
        column = item.column()
        if column == 7:
            result_start = self.events_table.item(row, 3).text()
            self.events_table.blockSignals(True)
            self.events_table.item(row, 5).setText(timestamp_plus_hours(result_start, item.text()))
            self.events_table.blockSignals(False)
        elif column == 8:
            result_end = self.events_table.item(row, 4).text()
            self.events_table.blockSignals(True)
            self.events_table.item(row, 6).setText(timestamp_minus_hours(result_end, item.text()))
            self.events_table.blockSignals(False)
        elif column == 5:
            result_start = self.events_table.item(row, 3).text()
            self.events_table.blockSignals(True)
            self.events_table.item(row, 7).setText(hours_between(result_start, item.text()))
            self.events_table.blockSignals(False)
        elif column == 6:
            result_end = self.events_table.item(row, 4).text()
            self.events_table.blockSignals(True)
            self.events_table.item(row, 8).setText(hours_between(item.text(), result_end))
            self.events_table.blockSignals(False)
        self._pull_current_gauge()
        self.refresh_gauge_table()
        self.changed.emit()

    def _gauge_selection_changed(self) -> None:
        if self._updating:
            return
        self._pull_current_gauge()
        indexes = self.gauge_table.selectionModel().selectedRows()
        self.selected_gauge = indexes[0].row() if indexes else -1
        self.refresh_editor()

    def _alias_finished(self) -> None:
        if self._updating:
            return
        self._pull_current_gauge()
        self.refresh_gauge_table()
        self.changed.emit()

    def _pull_current_gauge(self) -> None:
        if self.selected_gauge < 0 or self.selected_gauge >= len(self.gauges):
            return
        gauge = self.gauges[self.selected_gauge]
        gauge["alias"] = self.alias_edit.text()
        events = []
        for row in range(self.events_table.rowCount()):
            use_item = self.events_table.item(row, 0)
            if use_item is None or use_item.checkState() != Qt.CheckState.Checked:
                continue
            name = self.events_table.item(row, 1).text()
            start = self.events_table.item(row, 5).text()
            end = self.events_table.item(row, 6).text()
            events.append({"name": name, "start": start, "end": end})
        gauge["events"] = events

    def _default_events(self) -> list[dict[str, Any]]:
        return [
            {
                "name": str(result.get("name") or ""),
                "start": text_timestamp(result.get("start_time")),
                "end": text_timestamp(result.get("end_time")),
            }
            for result in self.config.get("results", [])
            if result.get("name")
        ]

    def _next_gauge_alias(self) -> str:
        existing = {str(gauge.get("alias") or "") for gauge in self.gauges}
        index = len(self.gauges) + 1
        while f"Gauge {index}" in existing:
            index += 1
        return f"Gauge {index}"


class SignalDialog(QDialog):
    def __init__(self, signal: dict[str, Any], config: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Signal")
        self.resize(780, 620)
        self.signal = copy.deepcopy(signal)
        self.config = normalize_config(config)
        self._build_layout()
        self._fill()

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.alias_edit = QLineEdit()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            "Observed only",
            "Simulated only",
            "Observed + simulated",
            "Combined simulated",
            "Observed + combined",
        ])
        self.mode_combo.currentTextChanged.connect(self._mode_changed)
        form.addRow("alias", self.alias_edit)
        form.addRow("mode", self.mode_combo)
        layout.addLayout(form)

        self.observed_group = QWidget()
        observed_form = QFormLayout(self.observed_group)
        self.source_combo = QComboBox()
        self.source_combo.addItems(sorted(self.config.get("monitoring_sources", {}).keys()))
        self.column_edit = QLineEdit()
        self.scale_edit = QLineEdit("1.0")
        self.offset_edit = QLineEdit("0.0")
        observed_form.addRow("observed source", self.source_combo)
        observed_form.addRow("observed column", self.column_edit)
        observed_form.addRow("scale", self.scale_edit)
        observed_form.addRow("offset", self.offset_edit)
        layout.addWidget(self.observed_group)

        self.simple_group = QWidget()
        simple_form = QFormLayout(self.simple_group)
        self.element_type_combo = QComboBox()
        self.element_type_combo.addItems(ELEMENT_TYPES)
        self.muid_edit = QLineEdit()
        self.quantity_combo = QComboBox()
        self.quantity_combo.setEditable(True)
        self.quantity_combo.addItems(QUANTITIES)
        self.chainage_edit = QLineEdit()
        simple_form.addRow("element type", self.element_type_combo)
        simple_form.addRow("muid", self.muid_edit)
        simple_form.addRow("quantity", self.quantity_combo)
        simple_form.addRow("chainage", self.chainage_edit)
        layout.addWidget(self.simple_group)

        self.terms_table = make_table(["operation", "element_type", "muid", "quantity", "chainage"])
        self.terms_table.setMaximumHeight(180)
        term_buttons = QHBoxLayout()
        self.add_term_button = mark_green_button(QPushButton("Add Term"))
        self.add_term_button.clicked.connect(self.add_term)
        self.delete_term_button = mark_green_button(QPushButton("Delete Term"))
        self.delete_term_button.clicked.connect(self.delete_term)
        term_buttons.addWidget(self.add_term_button)
        term_buttons.addWidget(self.delete_term_button)
        term_buttons.addStretch(1)
        layout.addLayout(term_buttons)
        layout.addWidget(self.terms_table)

        actions = QHBoxLayout()
        actions.addStretch(1)
        ok = mark_green_button(QPushButton("OK"))
        cancel = mark_green_button(QPushButton("Cancel"))
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        actions.addWidget(ok)
        actions.addWidget(cancel)
        layout.addLayout(actions)

    def _fill(self) -> None:
        self.alias_edit.setText(str(self.signal.get("alias") or ""))
        observed = self.signal.get("observed")
        simulated = self.signal.get("simulated")
        if observed and simulated:
            self.mode_combo.setCurrentText("Observed + combined" if simulated.get("terms") else "Observed + simulated")
        elif observed:
            self.mode_combo.setCurrentText("Observed only")
        elif simulated and simulated.get("terms"):
            self.mode_combo.setCurrentText("Combined simulated")
        else:
            self.mode_combo.setCurrentText("Simulated only")
        if observed:
            self.source_combo.setCurrentText(str(observed.get("source") or ""))
            self.column_edit.setText(str(observed.get("column") or ""))
            self.scale_edit.setText(str(observed.get("scale", 1.0)))
            self.offset_edit.setText(str(observed.get("offset", 0.0)))
        if simulated and not simulated.get("terms"):
            self.element_type_combo.setCurrentText(str(simulated.get("element_type") or "link"))
            self.muid_edit.setText(str(simulated.get("muid") or ""))
            self.quantity_combo.setCurrentText(str(simulated.get("quantity") or "Discharge"))
            self.chainage_edit.setText(str(simulated.get("chainage") or ""))
        terms = (simulated or {}).get("terms") or []
        self.terms_table.setRowCount(len(terms))
        for row, term in enumerate(terms):
            for column, key in enumerate(["operation", "element_type", "muid", "quantity", "chainage"]):
                self.terms_table.setItem(row, column, QTableWidgetItem(str(term.get(key) or "")))
        self._mode_changed()

    def _mode_changed(self) -> None:
        mode = self.mode_combo.currentText()
        self.observed_group.setVisible(mode in {"Observed only", "Observed + simulated", "Observed + combined"})
        self.simple_group.setVisible(mode in {"Simulated only", "Observed + simulated"})
        self.terms_table.setVisible(mode in {"Combined simulated", "Observed + combined"})
        self.add_term_button.setVisible(mode in {"Combined simulated", "Observed + combined"})
        self.delete_term_button.setVisible(mode in {"Combined simulated", "Observed + combined"})

    def add_term(self) -> None:
        row = self.terms_table.rowCount()
        self.terms_table.insertRow(row)
        for column, value in enumerate(["+", "link", "", "Discharge", ""]):
            self.terms_table.setItem(row, column, QTableWidgetItem(value))

    def delete_term(self) -> None:
        row = self.terms_table.currentRow()
        if row >= 0:
            self.terms_table.removeRow(row)

    def data(self) -> dict[str, Any]:
        mode = self.mode_combo.currentText()
        signal: dict[str, Any] = {"alias": self.alias_edit.text()}
        if mode in {"Observed only", "Observed + simulated", "Observed + combined"}:
            observed = {
                "source": self.source_combo.currentText(),
                "column": self.column_edit.text(),
            }
            if self.scale_edit.text() not in {"", "1", "1.0"}:
                observed["scale"] = float(self.scale_edit.text())
            if self.offset_edit.text() not in {"", "0", "0.0"}:
                observed["offset"] = float(self.offset_edit.text())
            signal["observed"] = observed
        if mode in {"Simulated only", "Observed + simulated"}:
            signal["simulated"] = self._simple_simulated()
        if mode in {"Combined simulated", "Observed + combined"}:
            signal["simulated"] = {"terms": self._terms()}
        return signal

    def _simple_simulated(self) -> dict[str, Any]:
        data = {
            "element_type": self.element_type_combo.currentText(),
            "muid": self.muid_edit.text(),
            "quantity": self.quantity_combo.currentText(),
        }
        if self.chainage_edit.text():
            data["chainage"] = float(self.chainage_edit.text())
        return data

    def _terms(self) -> list[dict[str, Any]]:
        terms: list[dict[str, Any]] = []
        keys = ["operation", "element_type", "muid", "quantity", "chainage"]
        for row in range(self.terms_table.rowCount()):
            term: dict[str, Any] = {}
            for column, key in enumerate(keys):
                item = self.terms_table.item(row, column)
                value = item.text() if item else ""
                if key == "chainage" and value:
                    term[key] = float(value)
                elif key != "chainage":
                    term[key] = value
            terms.append(term)
        return terms


def make_table(columns: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return table


def main() -> None:
    app = QApplication([sys.argv[0]])
    app.setApplicationName("CalibrationCheck")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

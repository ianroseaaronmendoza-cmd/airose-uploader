import json
import os
import secrets
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import requests
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QTextEdit, QLineEdit, QCheckBox, QMessageBox, QHeaderView,
    QAbstractItemView, QSplitter, QGroupBox, QApplication, QDialog,
    QProgressBar, QFrame
)
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtCore import Qt

from core.metadata_loader import (
    load_metadata,
    style_title_text,
    style_description_text,
    build_youtube_title_with_hashtags,
)
from core.stats_engine import compute_stats
from core.youtube_uploader import upload_video
from core.tiktok_uploader import PUBLISH_URL, upload_tiktok_video
from core.meta_uploader import upload_instagram_facebook_video
from core.tiktok_auth import (
    build_tiktok_auth_url,
    exchange_tiktok_code_for_token,
    get_tiktok_access_token,
    get_tiktok_oauth_settings,
    wait_for_tiktok_callback,
)
from core.pinterest_uploader import (
    PINTEREST_THEME_BOARD_IDS,
    explain_pinterest_readiness,
    has_pinterest_media_source,
    resolve_pinterest_board_id,
    resolve_pinterest_media_url,
    sanitize_pinterest_text,
    upload_pinterest_pin,
)
from list_pinterest_boards import fetch_all_boards, write_board_file
from refresh_pinterest_token import (
    _build_auth_url,
    _exchange_code_for_token,
    _load_oauth_creds,
    _save_token_cache,
    _update_pinterest_access_token,
    _wait_for_callback,
)

# Compact style applied to the whole window
_STYLESHEET = """
    QMainWindow { background: #1e1e1e; }
    QGroupBox   { font-weight: bold; margin-top: 6px; padding: 8px 4px; }
    QGroupBox::title { subcontrol-origin: margin; left: 8px; }
    QTableWidget { gridline-color: #3a3a3a; }
    QPushButton  { padding: 4px 14px; min-height: 24px; }
    QLineEdit, QTextEdit { padding: 2px 4px; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Airose Uploader - Phase 1")
        self.resize(1100, 800)
        self.setMinimumSize(700, 600)
        self.setStyleSheet(_STYLESHEET)

        self.assets = []
        self.current_asset = None
        self._approve_checkboxes: list[QCheckBox] = []
        self.show_only_available = False  # filter state
        self._tiktok_connected = False
        self._pinterest_board_names = self._load_pinterest_board_names()
        self._pinterest_connected = False

        self._build_ui()
        self.refresh_data()

    @staticmethod
    def _mask_secret(value: str, keep: int = 4) -> str:
        if not value:
            return "(missing)"
        if len(value) <= keep * 2:
            return "*" * len(value)
        return f"{value[:keep]}...{value[-keep:]}"

    def _load_pinterest_oauth_settings(self) -> dict:
        path = Path(__file__).resolve().parent.parent / "pinterest_oauth_credentials.json"
        if not path.exists():
            return {}

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

        return data if isinstance(data, dict) else {}

    # ================= UI =================

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Dashboard bar
        dash = QHBoxLayout()
        self.dashboard_label = QLabel()
        self.dashboard_label.setFont(QFont("Segoe UI", 9))
        
        # Filter checkboxes
        self.filter_unapproved = QCheckBox("Hide approved")
        self.filter_unapproved.setFont(QFont("Segoe UI", 9))
        self.filter_unapproved.stateChanged.connect(self.refresh_data)
        
        self.filter_uploaded = QCheckBox("Hide uploaded")
        self.filter_uploaded.setFont(QFont("Segoe UI", 9))
        self.filter_uploaded.stateChanged.connect(self.refresh_data)
        
        # Preset counters
        self.preset_label = QLabel()
        self.preset_label.setFont(QFont("Segoe UI", 9))
        
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(90)
        refresh_btn.clicked.connect(self.refresh_data)
        
        dash.addWidget(self.dashboard_label)
        dash.addStretch()
        dash.addWidget(self.preset_label)
        dash.addWidget(self.filter_unapproved)
        dash.addWidget(self.filter_uploaded)
        dash.addWidget(refresh_btn)
        root.addLayout(dash)

        # Splitter: table (top) + inspector (bottom)
        splitter = QSplitter(Qt.Orientation.Vertical)
        root.addWidget(splitter, stretch=1)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Mode", "Duration", "Video", "Approved", "YT", "TT", "IG/FB", "PIN"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.verticalHeader().setVisible(False)
        self.table.cellClicked.connect(self.load_inspector)
        splitter.addWidget(self.table)

        # Inspector group
        inspector = QGroupBox("Inspector")
        insp_layout = QVBoxLayout(inspector)
        insp_layout.setContentsMargins(6, 10, 6, 6)
        insp_layout.setSpacing(4)

        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Video title...")
        self.desc_edit = QTextEdit()
        self.desc_edit.setPlaceholderText("Video description...")
        self.desc_edit.setMaximumHeight(80)
        self.tt_connection_label = QLabel("Not connected")
        self.tt_connection_label.setWordWrap(True)
        self.pin_board_edit = QLineEdit()
        self.pin_board_edit.setPlaceholderText("Optional Pinterest board ID override...")
        self.pin_board_edit.textChanged.connect(self._update_pinterest_route_label)
        self.pin_section_edit = QLineEdit()
        self.pin_section_edit.setPlaceholderText("Optional Pinterest board section ID...")
        self.pin_link_edit = QLineEdit()
        self.pin_link_edit.setPlaceholderText("Optional Pinterest click-through URL...")
        self.pin_alt_text_edit = QLineEdit()
        self.pin_alt_text_edit.setPlaceholderText("Optional Pinterest alt text...")
        self.pin_route_label = QLabel("Automatic by theme")
        self.pin_route_label.setWordWrap(True)
        self.pin_status_label = QLabel("Select an asset")
        self.pin_status_label.setWordWrap(True)
        self.pin_connection_label = QLabel("Not connected")
        self.pin_connection_label.setWordWrap(True)
        form.addRow("Title:", self.title_edit)
        form.addRow("Desc:", self.desc_edit)
        form.addRow("TikTok Auth:", self.tt_connection_label)
        form.addRow("Pin Auth:", self.pin_connection_label)
        form.addRow("Pin Board:", self.pin_board_edit)
        form.addRow("Pin Route:", self.pin_route_label)
        form.addRow("Pin Status:", self.pin_status_label)
        form.addRow("Pin Section:", self.pin_section_edit)
        form.addRow("Pin Link:", self.pin_link_edit)
        form.addRow("Pin Alt:", self.pin_alt_text_edit)
        insp_layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.save_btn = QPushButton("Save Metadata")
        self.save_btn.clicked.connect(self.save_changes)
        self.upload_btn = QPushButton("Upload to YouTube")
        self.upload_btn.clicked.connect(self.upload_to_youtube)
        self.upload_btn.setEnabled(False)
        self.upload_tt_btn = QPushButton("Upload to TikTok")
        self.upload_tt_btn.clicked.connect(self.upload_to_tiktok)
        self.upload_tt_btn.setEnabled(False)
        self.tiktok_demo_checkbox = QCheckBox("TikTok Demo Mode")
        self.tiktok_demo_checkbox.setChecked(False)
        self.tiktok_demo_checkbox.setToolTip(
            "Authorize with TikTok, run the real sandbox init call, then simulate only the final publish step."
        )
        self.tiktok_demo_checkbox.stateChanged.connect(self._update_tiktok_button_label)
        self.upload_igfb_btn = QPushButton("Upload to IG/FB")
        self.upload_igfb_btn.clicked.connect(self.upload_to_instagram_facebook)
        self.upload_igfb_btn.setEnabled(False)
        self.connect_pin_btn = QPushButton("Connect Pinterest")
        self.connect_pin_btn.clicked.connect(self.connect_pinterest)
        self.upload_pin_btn = QPushButton("Upload to Pinterest")
        self.upload_pin_btn.clicked.connect(self.upload_to_pinterest)
        self.upload_pin_btn.setEnabled(False)
        btn_row.addStretch()
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.upload_btn)
        btn_row.addWidget(self.tiktok_demo_checkbox)
        btn_row.addWidget(self.upload_tt_btn)
        btn_row.addWidget(self.upload_igfb_btn)
        btn_row.addWidget(self.connect_pin_btn)
        btn_row.addWidget(self.upload_pin_btn)
        insp_layout.addLayout(btn_row)

        splitter.addWidget(inspector)
        splitter.setStretchFactor(0, 3)   # table gets more room
        splitter.setStretchFactor(1, 1)
        self._update_tiktok_button_label()
        self._update_pinterest_button_label()

    # ================= DATA =================

    def _compute_preset_stats(self) -> dict:
        """Compute counts for each preset type."""
        preset_counts = {"faith": 0, "love": 0, "sentimental": 0, "neutral": 0}
        
        for asset in self.assets:
            try:
                with open(asset.metadata_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    preset = data.get("preset", "").strip().lower()
                    if preset in preset_counts:
                        preset_counts[preset] += 1
            except (FileNotFoundError, json.JSONDecodeError):
                continue
        
        return preset_counts

    @staticmethod
    def _is_asset_approved(upload_status: dict) -> bool:
        for platform in ("youtube", "tiktok", "instagram_facebook", "pinterest"):
            if upload_status.get(platform, {}).get("approved", False):
                return True
        return False

    @staticmethod
    def _set_asset_approved(upload_status: dict, approved: bool) -> None:
        for platform in ("youtube", "tiktok", "instagram_facebook", "pinterest"):
            upload_status.setdefault(platform, {})
            upload_status[platform]["approved"] = approved

    @staticmethod
    def _has_any_uploaded(asset) -> bool:
        yt_uploaded = asset.upload_status.get("youtube", {}).get("uploaded", False)
        tt_uploaded = asset.upload_status.get("tiktok", {}).get("uploaded", False)
        igfb_status = asset.upload_status.get("instagram_facebook", {})
        pin_uploaded = asset.upload_status.get("pinterest", {}).get("uploaded", False)
        ig_uploaded = bool(igfb_status.get("instagram_media_id"))
        fb_uploaded = bool(igfb_status.get("facebook_video_id"))
        return yt_uploaded or tt_uploaded or ig_uploaded or fb_uploaded or pin_uploaded

    def _get_display_assets(self) -> list:
        display_assets = []
        for asset in self.assets:
            approved = self._is_asset_approved(asset.upload_status)
            if self.filter_unapproved.isChecked() and approved:
                continue
            if self.filter_uploaded.isChecked() and self._has_any_uploaded(asset):
                continue
            display_assets.append(asset)
        return display_assets

    @staticmethod
    def _load_pinterest_board_names() -> dict[str, str]:
        board_file = Path(__file__).resolve().parent.parent / "pinterest_boards.json"
        try:
            data = json.loads(board_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

        boards = data.get("boards", [])
        if not isinstance(boards, list):
            return {}

        mapping: dict[str, str] = {}
        for item in boards:
            if not isinstance(item, dict):
                continue
            board_id = str(item.get("id", "")).strip()
            name = str(item.get("name", "")).strip()
            if board_id and name:
                mapping[board_id] = name
        return mapping

    def _format_pinterest_route_text(self, data: dict | None = None) -> str:
        metadata = data or {}
        manual_board_id = self.pin_board_edit.text().strip()
        effective_board_id = resolve_pinterest_board_id(metadata, explicit_board_id=manual_board_id)
        if not effective_board_id:
            return "No board resolved"

        board_name = self._pinterest_board_names.get(effective_board_id, "Unknown board")
        if manual_board_id:
            return f"Manual override -> {board_name} ({effective_board_id})"

        theme = str(
            metadata.get("preset")
            or metadata.get("theme")
            or metadata.get("pinterest_theme")
            or ""
        ).strip().lower()
        if theme in PINTEREST_THEME_BOARD_IDS:
            return f"Auto theme '{theme}' -> {board_name} ({effective_board_id})"

        return f"Resolved -> {board_name} ({effective_board_id})"

    def _update_pinterest_route_label(self, _text: str = "") -> None:
        if not self.current_asset:
            self.pin_route_label.setText("Automatic by theme")
            return

        try:
            with open(self.current_asset.metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

        self.pin_route_label.setText(self._format_pinterest_route_text(data))

    def refresh_data(self):
        self.assets = load_metadata()
        stats = compute_stats(self.assets)
        preset_stats = self._compute_preset_stats()

        self.dashboard_label.setText(
            f"Total: {stats['total']} | "
            f"Valid: {stats['valid']} | "
            f"Missing: {stats['missing']} | "
            f"YT Uploaded: {stats['yt_uploaded']} | "
            f"TT Uploaded: {stats['tt_uploaded']} | "
            f"IG Uploaded: {stats['ig_uploaded']} | "
            f"PIN Uploaded: {stats['pin_uploaded']}"
        )

        self.preset_label.setText(
            f"Faith: {preset_stats['faith']} | "
            f"Love: {preset_stats['love']} | "
            f"Sentimental: {preset_stats['sentimental']} | "
            f"Neutral: {preset_stats['neutral']}"
        )

        self.populate_table()

    # helper: centered checkbox widget for a table cell
    def _make_centered_checkbox(self, checked: bool, asset) -> QWidget:
        cb = QCheckBox()
        cb.blockSignals(True)  # Block signals while setting initial state
        cb.setChecked(checked)
        cb.blockSignals(False)  # Re-enable signals
        cb.stateChanged.connect(lambda _state, a=asset, c=cb: self._on_approve_toggled(a, c))
        container = QWidget()
        lay = QHBoxLayout(container)
        lay.addWidget(cb)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setContentsMargins(0, 0, 0, 0)
        return container

    def _on_approve_toggled(self, asset, checkbox):
        """When an asset's Approved checkbox changes, save the status and refresh."""
        new_approved_state = checkbox.isChecked()
        
        # Update metadata file
        try:
            with open(asset.metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            upload_status = data.setdefault("upload_status", {})
            self._set_asset_approved(upload_status, new_approved_state)
            
            with open(asset.metadata_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving approved status: {e}")
            return
        
        # Reload data and refresh display
        self.assets = load_metadata()
        self.populate_table()
        
        # Clear inspector if current asset would be filtered out
        if self.current_asset and self.current_asset.id == asset.id:
            if self.filter_unapproved.isChecked() and new_approved_state:
                self.title_edit.setText("")
                self.desc_edit.setText("")
                self.pin_board_edit.setText("")
                self.pin_section_edit.setText("")
                self.pin_link_edit.setText("")
                self.pin_alt_text_edit.setText("")
                self.current_asset = None
                self.update_upload_button_state()

    def populate_table(self):
        display_assets = self._get_display_assets()

        self.table.setRowCount(len(display_assets))

        # Column sizing: stretch ID, fit the rest
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)       # ID
        for col in range(1, self.table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        for row, asset in enumerate(display_assets):
            self.table.setItem(row, 0, QTableWidgetItem(asset.id))
            self.table.setItem(row, 1, QTableWidgetItem(asset.production_mode))
            self.table.setItem(row, 2, QTableWidgetItem(f"{asset.duration:.1f}s"))

            # Video status
            video_item = QTableWidgetItem("OK" if asset.video_exists else "Missing")
            video_item.setBackground(QColor(0, 100, 0) if asset.video_exists else QColor(100, 0, 0))
            self.table.setItem(row, 3, video_item)

            # Approved checkbox (col 4)
            yt_status = asset.upload_status.get("youtube", {})
            approved = self._is_asset_approved(asset.upload_status)
            self.table.setCellWidget(row, 4, self._make_centered_checkbox(approved, asset))

            # YouTube upload status (col 5)
            uploaded = yt_status.get("uploaded", False)
            yt_item = QTableWidgetItem("OK" if uploaded else ("WAIT" if approved else "-"))
            if uploaded:
                yt_item.setBackground(QColor(0, 150, 0))
            elif approved:
                yt_item.setBackground(QColor(150, 120, 0))
            else:
                yt_item.setBackground(QColor(80, 80, 80))
            self.table.setItem(row, 5, yt_item)

            # TT (col 6)
            tt_uploaded = asset.upload_status.get("tiktok", {}).get("uploaded", False)
            tt_item = QTableWidgetItem("OK" if tt_uploaded else "-")
            tt_item.setBackground(QColor(0, 150, 0) if tt_uploaded else QColor(80, 80, 80))
            self.table.setItem(row, 6, tt_item)

            # IG/FB (col 7)
            ig_uploaded = asset.upload_status.get("instagram_facebook", {}).get("uploaded", False)
            ig_item = QTableWidgetItem("OK" if ig_uploaded else "-")
            ig_item.setBackground(QColor(0, 150, 0) if ig_uploaded else QColor(80, 80, 80))
            self.table.setItem(row, 7, ig_item)

            pin_uploaded = asset.upload_status.get("pinterest", {}).get("uploaded", False)
            pin_item = QTableWidgetItem("OK" if pin_uploaded else "-")
            pin_item.setBackground(QColor(0, 150, 0) if pin_uploaded else QColor(80, 80, 80))
            self.table.setItem(row, 8, pin_item)

    # ================= INSPECTOR =================

    def load_inspector(self, row, _):
        filtered_assets = self._get_display_assets()
        
        if row >= len(filtered_assets):
            return
        asset = filtered_assets[row]
        
        self.current_asset = asset
        self._current_row = row

        with open(asset.metadata_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.title_edit.setText(data.get("title", ""))
        self.desc_edit.setText(data.get("description", ""))
        self.pin_board_edit.setText(data.get("pinterest_board_id", ""))
        self.pin_section_edit.setText(data.get("pinterest_board_section_id", ""))
        self.pin_link_edit.setText(data.get("pinterest_link", ""))
        self.pin_alt_text_edit.setText(data.get("pinterest_alt_text", ""))
        self.pin_route_label.setText(self._format_pinterest_route_text(data))

        self.update_upload_button_state()

    def save_changes(self):
        if not self.current_asset:
            return

        with open(self.current_asset.metadata_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        normalized_title = style_title_text(self.title_edit.text())
        normalized_description = style_description_text(self.desc_edit.toPlainText())

        data["title"] = normalized_title
        data["description"] = normalized_description
        data["pinterest_board_id"] = self.pin_board_edit.text().strip()
        data["pinterest_board_section_id"] = self.pin_section_edit.text().strip()
        data["pinterest_link"] = self.pin_link_edit.text().strip()
        data["pinterest_alt_text"] = self.pin_alt_text_edit.text().strip()

        # Reflect normalized values in the editor immediately.
        self.title_edit.setText(normalized_title)
        self.desc_edit.setText(normalized_description)
        self.pin_route_label.setText(self._format_pinterest_route_text(data))

        # The approved state is already stored in the asset's upload_status
        # No need to read from checkbox since it's already updated via _on_approve_toggled

        with open(self.current_asset.metadata_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        self.refresh_data()

    # ================= UPLOAD =================

    @staticmethod
    def _get_public_video_url(data: dict) -> str | None:
        return (
            data.get("youtube_video_url")
            or data.get("public_video_url")
            or data.get("instagram_video_url")
            or data.get("google_drive_link")
            or data.get("google_drive_url")
        )

    def _get_pinterest_media_url(self, data: dict) -> str | None:
        return resolve_pinterest_media_url(
            data,
            video_path=getattr(self.current_asset, "video_path", ""),
        )

    def update_upload_button_state(self):
        if not self.current_asset:
            self.upload_btn.setEnabled(False)
            self.upload_tt_btn.setEnabled(False)
            self.upload_igfb_btn.setEnabled(False)
            self.upload_pin_btn.setEnabled(False)
            self.upload_pin_btn.setToolTip("")
            self.pin_status_label.setText("Select an asset")
            return

        approved = self._is_asset_approved(self.current_asset.upload_status)
        has_video = self.current_asset.video_exists

        # YouTube
        yt_status = self.current_asset.upload_status.get("youtube", {})
        yt_uploaded = yt_status.get("uploaded", False)
        youtube_video_url = None
        try:
            with open(self.current_asset.metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            youtube_video_url = self._get_public_video_url(data)
        except Exception:
            youtube_video_url = None
        has_youtube_source = has_video or bool(youtube_video_url)
        self.upload_btn.setEnabled(approved and has_youtube_source and not yt_uploaded)

        # TikTok
        tt_status = self.current_asset.upload_status.get("tiktok", {})
        tt_uploaded = tt_status.get("uploaded", False)
        tiktok_demo_enabled = getattr(self, "tiktok_demo_checkbox", None) and self.tiktok_demo_checkbox.isChecked()
        if tiktok_demo_enabled:
            self.upload_tt_btn.setEnabled(has_video and approved)
            self.upload_tt_btn.setToolTip(
                "Authorizes with TikTok sandbox as needed, performs the real init call, then simulates the final publish result."
            )
            self.tt_connection_label.setText(
                "Authorized" if self._tiktok_connected else "Will authorize during upload."
            )
        else:
            self.upload_tt_btn.setEnabled(has_video and approved and not tt_uploaded)
            self.upload_tt_btn.setToolTip("")
            self.tt_connection_label.setText("Authorized" if self._tiktok_connected else "Not connected")

        # IG/FB combined
        igfb_status = self.current_asset.upload_status.get("instagram_facebook", {})
        fb_uploaded = bool(igfb_status.get("facebook_video_id"))
        ig_uploaded = bool(igfb_status.get("instagram_media_id"))
        self.upload_igfb_btn.setEnabled(has_video and approved and (not fb_uploaded or not ig_uploaded))

        pin_uploaded = self.current_asset.upload_status.get("pinterest", {}).get("uploaded", False)
        try:
            with open(self.current_asset.metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            has_pin_source = has_pinterest_media_source(
                data,
                video_path=self.current_asset.video_path,
            )
            pin_reason = explain_pinterest_readiness(
                data,
                approved=approved,
                already_uploaded=pin_uploaded,
                video_path=self.current_asset.video_path,
            )
        except Exception:
            has_pin_source = False
            pin_reason = "Unable to read metadata for Pinterest upload."
        can_upload_pin = approved and has_pin_source and not pin_uploaded
        self.upload_pin_btn.setEnabled(can_upload_pin)
        self.upload_pin_btn.setToolTip(pin_reason)
        self.pin_status_label.setText(pin_reason)
        self._update_tiktok_button_label()
        self._update_pinterest_button_label()

    def _update_tiktok_button_label(self):
        demo_enabled = getattr(self, "tiktok_demo_checkbox", None) and self.tiktok_demo_checkbox.isChecked()
        if demo_enabled:
            self.upload_tt_btn.setText("Authorize + Demo TikTok Upload")
            self.upload_tt_btn.setToolTip(
                "Runs TikTok authorization if needed, calls the real sandbox init endpoint, and simulates the final publish."
            )
        else:
            self.upload_tt_btn.setText("Upload to TikTok")
            self.upload_tt_btn.setToolTip("")

    def _update_pinterest_button_label(self):
        self.upload_pin_btn.setText("Upload to Pinterest")
        self.upload_pin_btn.setToolTip("")

    def _build_tiktok_demo_dialog(self, title: str, subtitle: str) -> tuple[QDialog, QLabel, QProgressBar]:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(420)
        dialog.setStyleSheet(
            """
            QDialog { background: #121217; color: #f7f7f8; }
            QFrame#card { background: #18181f; border: 1px solid #2a2a34; border-radius: 16px; }
            QLabel#brand { color: #25f4ee; font-size: 20px; font-weight: 700; }
            QLabel#subtitle { color: #c9c9d1; font-size: 12px; }
            QLabel#status { color: #ffffff; font-size: 13px; font-weight: 600; }
            QLabel#meta { color: #9d9daa; font-size: 11px; }
            QProgressBar {
                background: #0d0d12;
                border: 1px solid #30303a;
                border-radius: 8px;
                height: 16px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #25f4ee, stop:1 #fe2c55);
                border-radius: 7px;
            }
            QPushButton {
                background: #fe2c55;
                color: white;
                border: none;
                border-radius: 18px;
                padding: 8px 18px;
                min-height: 20px;
                font-weight: 700;
            }
            QPushButton:hover { background: #ff4a6e; }
            """
        )

        root = QVBoxLayout(dialog)
        root.setContentsMargins(18, 18, 18, 18)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(10)

        brand = QLabel("TikTok Demo")
        brand.setObjectName("brand")
        card_layout.addWidget(brand)

        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("subtitle")
        subtitle_label.setWordWrap(True)
        card_layout.addWidget(subtitle_label)

        status_label = QLabel("Preparing demo flow...")
        status_label.setObjectName("status")
        status_label.setWordWrap(True)
        card_layout.addWidget(status_label)

        meta_label = QLabel("Mock UI only. No TikTok API calls are made.")
        meta_label.setObjectName("meta")
        meta_label.setWordWrap(True)
        card_layout.addWidget(meta_label)

        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)
        card_layout.addWidget(progress)

        root.addWidget(card)
        return dialog, status_label, progress

    def _run_tiktok_demo_auth_dialog(self) -> bool:
        oauth_settings = get_tiktok_oauth_settings()
        redirect_uri = oauth_settings.get("redirect_uri", "")
        scopes = oauth_settings.get("scopes", "")
        website_url = oauth_settings.get("website_url", "") or "(missing)"
        auth_url = (
            "https://www.tiktok.com/v2/auth/authorize/"
            f"?client_key={self._mask_secret(oauth_settings.get('client_key', ''), keep=3)}"
            "&response_type=code"
            f"&scope={scopes}"
            f"&redirect_uri={redirect_uri}"
        )

        dialog, _, _ = self._build_tiktok_demo_dialog(
            "TikTok Demo Sign-In",
            "Reviewer-facing simulation of the TikTok authorization screen using the app's configured OAuth values.",
        )

        card = dialog.findChild(QFrame, "card")
        card_layout = card.layout()
        account_label = QLabel("Continue as @airose_demo_creator")
        account_label.setObjectName("status")
        card_layout.addWidget(account_label)

        config_view = QTextEdit()
        config_view.setReadOnly(True)
        config_view.setMinimumHeight(120)
        config_view.setPlainText(
            "\n".join(
                [
                    f"Website URL: {website_url}",
                    f"Redirect URI: {redirect_uri or '(missing)'}",
                    f"Requested scopes: {scopes or '(missing)'}",
                    "",
                    "Reviewer note: the demo must match the exact website, redirect URI, and selected scopes configured in TikTok Developer.",
                ]
            )
        )
        card_layout.addWidget(config_view)

        auth_url_view = QTextEdit()
        auth_url_view.setReadOnly(True)
        auth_url_view.setMinimumHeight(90)
        auth_url_view.setPlainText(auth_url)
        card_layout.addWidget(auth_url_view)

        button_row = QHBoxLayout()
        button_row.addStretch()
        cancel_button = QPushButton("Cancel")
        cancel_button.setStyleSheet(
            "QPushButton { background: #2a2a34; } QPushButton:hover { background: #3a3a46; }"
        )
        continue_button = QPushButton("Continue")
        button_row.addWidget(cancel_button)
        button_row.addWidget(continue_button)
        card_layout.addLayout(button_row)

        cancel_button.clicked.connect(dialog.reject)
        continue_button.clicked.connect(dialog.accept)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _run_tiktok_demo_upload_dialog(
        self,
        title: str,
        subtitle: str,
        steps: list[tuple[int, str]],
    ) -> None:
        dialog, status_label, progress = self._build_tiktok_demo_dialog(title, subtitle)
        dialog.show()
        QApplication.processEvents()

        for value, message in steps:
            status_label.setText(message)
            progress.setValue(value)
            QApplication.processEvents()
            time.sleep(0.45)

        dialog.accept()

    def _run_tiktok_sequence_step(
        self,
        step_number: int,
        total_steps: int,
        title: str,
        subtitle: str,
        heading: str,
        body: str,
        button_text: str = "Continue",
    ) -> bool:
        dialog, _, _ = self._build_tiktok_demo_dialog(title, subtitle)
        card = dialog.findChild(QFrame, "card")
        card_layout = card.layout()

        progress_label = QLabel(f"Step {step_number} of {total_steps}")
        progress_label.setObjectName("status")
        card_layout.addWidget(progress_label)

        editor = QTextEdit()
        editor.setReadOnly(True)
        editor.setMinimumHeight(170)
        editor.setPlainText(f"{heading}\n\n{body}")
        card_layout.addWidget(editor)

        button_row = QHBoxLayout()
        button_row.addStretch()
        cancel_button = QPushButton("Cancel")
        cancel_button.setStyleSheet(
            "QPushButton { background: #2a2a34; } QPushButton:hover { background: #3a3a46; }"
        )
        continue_button = QPushButton(button_text)
        button_row.addWidget(cancel_button)
        button_row.addWidget(continue_button)
        card_layout.addLayout(button_row)

        cancel_button.clicked.connect(dialog.reject)
        continue_button.clicked.connect(dialog.accept)
        return dialog.exec() == QDialog.DialogCode.Accepted

    @staticmethod
    def _start_tiktok_callback_listener(redirect_uri: str) -> tuple[threading.Thread, dict, threading.Event]:
        result: dict = {}
        done = threading.Event()

        def _worker() -> None:
            try:
                result["code"] = wait_for_tiktok_callback(redirect_uri)
            except Exception as e:
                result["error"] = str(e)
            finally:
                done.set()

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return thread, result, done

    def connect_tiktok_for_demo(self) -> bool:
        try:
            auth_url, oauth_settings, code_verifier = build_tiktok_auth_url()
            redirect_uri = oauth_settings["redirect_uri"]
            scopes = oauth_settings["scopes"]
            website_url = oauth_settings.get("website_url", "") or "(missing)"
        except Exception as e:
            QMessageBox.critical(self, "TikTok Connect Failed", str(e))
            return False

        try:
            _, callback_result, callback_done = self._start_tiktok_callback_listener(redirect_uri)

            if not self._run_tiktok_sequence_step(
                1,
                4,
                "TikTok Authorization",
                "TikTok authorization starts automatically as part of the demo upload flow.",
                "Step 1: Authorize TikTok Access",
                "\n".join(
                    [
                        f"Website URL: {website_url}",
                        f"Redirect URI: {redirect_uri}",
                        f"Requested scopes: {scopes}",
                        "",
                        "Action: continue, then complete the TikTok login and permissions screens in the browser.",
                        "",
                        "OAuth URL:",
                        auth_url,
                    ]
                ),
                button_text="Open OAuth",
            ):
                return False

            webbrowser.open(auth_url, new=1, autoraise=True)

            if not self._run_tiktok_sequence_step(
                2,
                4,
                "TikTok OAuth Redirect",
                "Leave this visible while the browser completes sandbox auth.",
                "Step 2: Redirect Back To App",
                "\n".join(
                    [
                        f"Redirect URI used in app: {redirect_uri}",
                        "",
                        "Reviewer signal: browser should redirect back to the registered callback, then return to the desktop app.",
                    ]
                ),
                button_text="Wait For Redirect",
            ):
                return False

            if not callback_done.wait(timeout=180):
                raise RuntimeError("Timed out waiting for the TikTok OAuth callback on 127.0.0.1:8080.")
            if callback_result.get("error"):
                raise RuntimeError(str(callback_result["error"]))
            code = str(callback_result.get("code", "")).strip()
            if not code:
                raise RuntimeError("TikTok OAuth callback did not return an authorization code.")

            token_data = exchange_tiktok_code_for_token(code, code_verifier)
            self._tiktok_connected = True
            self.tt_connection_label.setText("Connected")

            return self._run_tiktok_sequence_step(
                3,
                4,
                "TikTok Token Exchange",
                "Real token exchange proof.",
                "Step 3: Exchange Authorization Code",
                "\n".join(
                    [
                        f"Callback received: {redirect_uri}?code={self._mask_secret(code, keep=3)}",
                        "POST https://open.tiktokapis.com/v2/oauth/token/",
                        "Access token received",
                        "",
                        f"Granted scope: {token_data.get('scope', scopes)}",
                    ]
                ),
                button_text="Done",
            )
        except Exception as e:
            QMessageBox.critical(self, "TikTok Connect Failed", str(e))
            return False

    def _run_tiktok_init_demo_call(self) -> dict:
        if not self.current_asset:
            raise RuntimeError("Select an asset before running the TikTok demo upload.")
        if not os.path.isfile(self.current_asset.video_path):
            raise FileNotFoundError(f"Video not found: {self.current_asset.video_path}")

        access_token = get_tiktok_access_token()
        file_size = os.path.getsize(self.current_asset.video_path)
        caption = style_title_text(self.title_edit.text()) or "(empty)"
        description = style_description_text(self.desc_edit.toPlainText())
        if description:
            caption = f"{caption}\n\n{description}"

        payload = {
            "post_info": {
                "title": caption[:2200],
                "privacy_level": "SELF_ONLY",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": file_size,
                "total_chunk_count": 1,
            },
        }
        response = requests.post(
            PUBLISH_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        error = data.get("error", {})
        if error and error.get("code") != "ok":
            raise RuntimeError(f"TikTok init failed: {error}")
        return {
            "request_payload": payload,
            "response": data,
            "publish_id": data.get("data", {}).get("publish_id", ""),
            "upload_url": data.get("data", {}).get("upload_url", ""),
        }

    def _run_tiktok_demo_upload(self):
        if not self._tiktok_connected and not self.connect_tiktok_for_demo():
            return

        init_result = self._run_tiktok_init_demo_call()
        publish_id = init_result.get("publish_id") or f"demo_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        upload_url = init_result.get("upload_url", "")

        if not self._run_tiktok_sequence_step(
            4,
            6,
            "TikTok Sandbox API Call",
            "Real sandbox API touchpoint for the review recording.",
            "Step 4: Initialize TikTok Direct Post API Request",
            "\n".join(
                [
                    f"POST {PUBLISH_URL}",
                    "",
                    "Request payload:",
                    json.dumps(init_result["request_payload"], indent=2),
                    "",
                    "Sandbox response received.",
                    f"publish_id: {publish_id}",
                    f"upload_url: {self._mask_secret(upload_url, keep=12) if upload_url else '(missing)'}",
                ]
            ),
            button_text="Show Simulated Upload",
        ):
            return

        if not self._run_tiktok_sequence_step(
            5,
            6,
            "TikTok Simulated Upload",
            "Only the final upload/publish result is simulated.",
            "Step 5: Simulated Upload Result",
            "\n".join(
                [
                    "Uploading video to TikTok sandbox... (Simulated for demo)",
                    "Finalizing TikTok publish... (Simulated for demo)",
                    "",
                    "The OAuth and video/init API call were real. The final upload result is simulated for the review recording.",
                ]
            ),
            button_text="Finish Demo",
        ):
            return

        self._run_tiktok_sequence_step(
            6,
            6,
            "TikTok Demo Complete",
            "Final demo confirmation.",
            "Step 6: Simulated TikTok Publish Success",
            "\n".join(
                [
                    f"Sandbox publish_id: {publish_id}",
                    "",
                    "Demo only. Metadata was not changed.",
                ]
            ),
            button_text="Close",
        )

    def _build_pinterest_dialog(self, title: str, subtitle: str) -> tuple[QDialog, QVBoxLayout]:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.resize(760, 640)
        dialog.setStyleSheet(
            """
            QDialog { background: #fff9f9; color: #261b1e; }
            QFrame#card { background: #ffffff; border: 1px solid #f2d9df; border-radius: 18px; }
            QLabel#brand { color: #e60023; font-size: 22px; font-weight: 800; }
            QLabel#subtitle { color: #6b4b53; font-size: 12px; }
            QLabel#section { color: #261b1e; font-size: 13px; font-weight: 700; }
            QLabel#meta { color: #6b4b53; font-size: 11px; }
            QTextEdit {
                background: #fffdfd;
                color: #261b1e;
                border: 1px solid #f0d3da;
                border-radius: 12px;
                padding: 8px;
                font-family: Consolas;
                font-size: 11px;
            }
            QPushButton {
                background: #e60023;
                color: white;
                border: none;
                border-radius: 18px;
                padding: 8px 18px;
                min-height: 20px;
                font-weight: 700;
            }
            QPushButton:hover { background: #bd081c; }
            """
        )

        root = QVBoxLayout(dialog)
        root.setContentsMargins(18, 18, 18, 18)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(12)

        brand = QLabel("Pinterest")
        brand.setObjectName("brand")
        card_layout.addWidget(brand)

        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("subtitle")
        subtitle_label.setWordWrap(True)
        card_layout.addWidget(subtitle_label)

        root.addWidget(card)
        return dialog, card_layout

    @staticmethod
    def _add_demo_text_block(layout: QVBoxLayout, heading: str, body: str) -> QTextEdit:
        heading_label = QLabel(heading)
        heading_label.setObjectName("section")
        layout.addWidget(heading_label)

        editor = QTextEdit()
        editor.setReadOnly(True)
        editor.setMinimumHeight(110)
        editor.setPlainText(body)
        layout.addWidget(editor)
        return editor

    def _run_pinterest_sequence_step(
        self,
        step_number: int,
        total_steps: int,
        title: str,
        subtitle: str,
        heading: str,
        body: str,
        button_text: str = "Continue",
    ) -> bool:
        dialog, layout = self._build_pinterest_dialog(title, subtitle)

        progress_label = QLabel(f"Step {step_number} of {total_steps}")
        progress_label.setObjectName("section")
        layout.addWidget(progress_label)

        self._add_demo_text_block(layout, heading, body)

        button_row = QHBoxLayout()
        button_row.addStretch()
        cancel_button = QPushButton("Cancel")
        cancel_button.setStyleSheet(
            "QPushButton { background: #f3f0f1; color: #261b1e; border: 1px solid #e0c9cf; }"
            " QPushButton:hover { background: #eadfe2; }"
        )
        continue_button = QPushButton(button_text)
        button_row.addWidget(cancel_button)
        button_row.addWidget(continue_button)
        layout.addLayout(button_row)

        cancel_button.clicked.connect(dialog.reject)
        continue_button.clicked.connect(dialog.accept)
        return dialog.exec() == QDialog.DialogCode.Accepted

    @staticmethod
    def _start_pinterest_callback_listener(redirect_uri: str, expected_state: str) -> tuple[threading.Thread, dict, threading.Event]:
        result: dict = {}
        done = threading.Event()

        def _worker() -> None:
            try:
                result["code"] = _wait_for_callback(redirect_uri, expected_state)
            except Exception as e:
                result["error"] = str(e)
            finally:
                done.set()

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return thread, result, done

    def connect_pinterest(self) -> bool:
        try:
            creds = _load_oauth_creds()
            state = secrets.token_urlsafe(24)
            auth_url = _build_auth_url(creds, state)
        except Exception as e:
            QMessageBox.critical(self, "Pinterest Connect Failed", str(e))
            return False

        try:
            _, callback_result, callback_done = self._start_pinterest_callback_listener(
                creds["redirect_uri"],
                state,
            )

            if not self._run_pinterest_sequence_step(
                1,
                4,
                "Connect Pinterest",
                "Explicit OAuth sequence for the review recording.",
                "Step 1: User Authenticates Via Pinterest OAuth",
                "\n".join(
                    [
                        "Action: click Connect Pinterest.",
                        "Expected browser view: Pinterest login page, then permissions screen.",
                        "",
                        "OAuth URL:",
                        auth_url,
                    ]
                ),
                button_text="Open OAuth",
            ):
                return False

            opened = webbrowser.open(auth_url, new=1, autoraise=True)
            if not self._run_pinterest_sequence_step(
                2,
                4,
                "Pinterest OAuth Redirect",
                "Leave this window visible while the browser completes auth.",
                "Step 2: Redirect Back To App",
                "\n".join(
                    [
                        "Waiting for localhost callback from Pinterest OAuth.",
                        f"Redirect URI: {creds['redirect_uri']}",
                        "",
                        "Reviewer signal: show the browser redirect back to the local callback.",
                        "If the browser did not open automatically, paste the OAuth URL into the browser manually.",
                    ]
                ),
                button_text="Wait For Redirect",
            ):
                return False

            if not callback_done.wait(timeout=180):
                raise RuntimeError(
                    "Timed out waiting for the Pinterest OAuth callback on localhost:8788."
                )
            if callback_result.get("error"):
                raise RuntimeError(str(callback_result["error"]))
            code = str(callback_result.get("code", "")).strip()
            if not code:
                raise RuntimeError("Pinterest OAuth callback did not return an authorization code.")

            token_data = _exchange_code_for_token(creds, code)
            _save_token_cache(token_data)
            _update_pinterest_access_token(str(token_data["access_token"]))
            if not self._run_pinterest_sequence_step(
                3,
                4,
                "Pinterest Token Exchange",
                "Real token exchange proof for the review recording.",
                "Step 3: Exchanging Authorization Code",
                "\n".join(
                    [
                        f"Callback received: {creds['redirect_uri']}?code={self._mask_secret(code, keep=3)}",
                        "Pinterest Connected Successfully",
                        "",
                        "Exchanging authorization code...",
                        "Access token received",
                    ]
                ),
                button_text="Fetch Boards",
            ):
                return False

            boards = fetch_all_boards()
            write_board_file(boards)
            self._pinterest_board_names = self._load_pinterest_board_names()
            self._pinterest_connected = True
            self.pin_connection_label.setText(f"Connected. {len(boards)} boards fetched.")
            self._update_pinterest_route_label()
            self.update_upload_button_state()

            board_lines = [
                "Fetching boards from Pinterest API...",
                "GET /v5/boards",
                "Boards retrieved successfully.",
            ]
            if boards:
                board_lines.extend(
                    f"- {board.get('name', 'Unknown')} ({board.get('id', '')})"
                    for board in boards[:10]
                )
            else:
                board_lines.append("- No boards returned")
            board_lines.extend(["", "Note: Production mode uses the live Pinterest API."])
            return self._run_pinterest_sequence_step(
                4,
                4,
                "Pinterest API Proof",
                "Real Pinterest API call confirmation.",
                "Step 4: Fetch Boards From Pinterest API",
                "\n".join(board_lines),
                button_text="Done",
            )
        except Exception as e:
            QMessageBox.critical(self, "Pinterest Connect Failed", str(e))
            return False

    def upload_to_youtube(self):
        if not self.current_asset:
            return

        try:
            with open(self.current_asset.metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            normalized_title = style_title_text(self.title_edit.text())
            normalized_description = style_description_text(self.desc_edit.toPlainText())
            youtube_title = build_youtube_title_with_hashtags(
                normalized_title,
                normalized_description,
            )
            youtube_video_url = self._get_public_video_url(data)
            video_id = upload_video(
                self.current_asset.video_path,
                youtube_title,
                normalized_description,
                video_url=youtube_video_url,
            )

            yt_status = data.setdefault("upload_status", {}).setdefault("youtube", {})
            yt_status["uploaded"] = True
            yt_status["uploaded_at"] = datetime.utcnow().isoformat()
            yt_status["video_id"] = video_id

            with open(self.current_asset.metadata_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

            QMessageBox.information(self, "Success", f"Uploaded successfully!\nVideo ID: {video_id}")
            self.refresh_data()

        except Exception as e:
            QMessageBox.critical(self, "Upload Failed", str(e))

    def upload_to_tiktok(self):
        if not self.current_asset:
            return

        try:
            if self.tiktok_demo_checkbox.isChecked():
                self._run_tiktok_demo_upload()
                return

            publish_id = upload_tiktok_video(
                self.current_asset.video_path,
                style_title_text(self.title_edit.text()),
                style_description_text(self.desc_edit.toPlainText())
            )

            with open(self.current_asset.metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            data.setdefault("upload_status", {}).setdefault("tiktok", {})
            data["upload_status"]["tiktok"]["uploaded"] = True
            data["upload_status"]["tiktok"]["uploaded_at"] = datetime.utcnow().isoformat()
            data["upload_status"]["tiktok"]["publish_id"] = publish_id

            with open(self.current_asset.metadata_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

            QMessageBox.information(self, "Success", f"TikTok upload complete!\nPublish ID: {publish_id}")
            self.refresh_data()

        except Exception as e:
            QMessageBox.critical(self, "TikTok Upload Failed", str(e))

    def upload_to_instagram_facebook(self):
        if not self.current_asset:
            return

        try:
            with open(self.current_asset.metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            igfb_status = data.setdefault("upload_status", {}).setdefault("instagram_facebook", {})
            do_fb = not bool(igfb_status.get("facebook_video_id"))
            do_ig = not bool(igfb_status.get("instagram_media_id"))
            if not do_fb and not do_ig:
                QMessageBox.information(self, "Info", "IG/FB already uploaded for this asset.")
                return

            instagram_video_url = (
                data.get("instagram_video_url")
                or data.get("public_video_url")
                or data.get("google_drive_link")
                or data.get("google_drive_url")
            )
            drive_folder_url = (
                data.get("google_drive_folder_url")
                or data.get("google_drive_folder_link")
                or data.get("drive_folder_url")
            )
            result = upload_instagram_facebook_video(
                self.current_asset.video_path,
                style_title_text(self.title_edit.text()),
                style_description_text(self.desc_edit.toPlainText()),
                instagram_video_url=instagram_video_url,
                drive_folder_url=drive_folder_url,
                upload_facebook=do_fb,
                upload_instagram=do_ig,
            )

            igfb_status["uploaded"] = bool(
                (result.get("facebook_video_id") or igfb_status.get("facebook_video_id"))
                or (result.get("instagram_media_id") or igfb_status.get("instagram_media_id"))
            )
            igfb_status["uploaded_at"] = datetime.utcnow().isoformat()
            if result.get("facebook_video_id"):
                igfb_status["facebook_video_id"] = result.get("facebook_video_id")
            if result.get("instagram_media_id"):
                igfb_status["instagram_media_id"] = result.get("instagram_media_id")

            with open(self.current_asset.metadata_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

            QMessageBox.information(
                self,
                "Success",
                "IG/FB upload complete.\n"
                f"Facebook video ID: {igfb_status.get('facebook_video_id')}\n"
                f"Instagram media ID: {igfb_status.get('instagram_media_id')}\n"
                f"{result.get('instagram_error') or ''}",
            )
            self.refresh_data()

        except Exception as e:
            QMessageBox.critical(self, "IG/FB Upload Failed", str(e))

    def upload_to_pinterest(self):
        if not self.current_asset:
            return

        try:
            with open(self.current_asset.metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            normalized_title = style_title_text(self.title_edit.text())
            normalized_description = style_description_text(self.desc_edit.toPlainText())
            pinterest_title = sanitize_pinterest_text(normalized_title)
            pinterest_description = sanitize_pinterest_text(normalized_description)
            effective_board_id = resolve_pinterest_board_id(
                data,
                explicit_board_id=self.pin_board_edit.text().strip(),
            )
            pinterest_media_source = data.get("pinterest_media_source")
            pinterest_media_url = resolve_pinterest_media_url(
                data,
                video_path=self.current_asset.video_path,
            ) or ""
            pinterest_link = (
                self.pin_link_edit.text().strip()
                or data.get("public_video_url")
                or data.get("youtube_video_url")
                or ""
            )
            pinterest_alt_text = self.pin_alt_text_edit.text().strip() or normalized_description
            pin_id = upload_pinterest_pin(
                title=pinterest_title,
                description=pinterest_description,
                video_path=self.current_asset.video_path,
                media_url=pinterest_media_url,
                link=pinterest_link,
                alt_text=pinterest_alt_text,
                board_id=effective_board_id,
                board_section_id=self.pin_section_edit.text().strip(),
                media_source=(
                    pinterest_media_source
                    if isinstance(pinterest_media_source, dict)
                    else None
                ),
                cover_image_key_frame_time=data.get("pinterest_cover_image_key_frame_time"),
            )

            pin_status = data.setdefault("upload_status", {}).setdefault("pinterest", {})
            pin_status["uploaded"] = True
            pin_status["uploaded_at"] = datetime.utcnow().isoformat()
            pin_status["pin_id"] = pin_id
            pin_status["board_id"] = effective_board_id or pin_status.get("board_id")
            pin_status["board_section_id"] = (
                self.pin_section_edit.text().strip() or pin_status.get("board_section_id")
            )
            pin_status["error"] = None
            data["pinterest_title"] = pinterest_title
            data["pinterest_description"] = pinterest_description
            data["pinterest_board_id"] = effective_board_id
            data["pinterest_board_section_id"] = self.pin_section_edit.text().strip()
            data["pinterest_link"] = self.pin_link_edit.text().strip()
            data["pinterest_alt_text"] = self.pin_alt_text_edit.text().strip()
            self.pin_board_edit.setText(effective_board_id)

            with open(self.current_asset.metadata_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

            QMessageBox.information(self, "Success", f"Pinterest pin created.\nPin ID: {pin_id}")
            self.refresh_data()

        except Exception as e:
            QMessageBox.critical(self, "Pinterest Upload Failed", str(e))



import json
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QTextEdit, QLineEdit, QCheckBox, QMessageBox, QHeaderView,
    QAbstractItemView, QSplitter, QGroupBox
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
from core.tiktok_uploader import upload_tiktok_video
from core.meta_uploader import upload_instagram_facebook_video

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
        self.resize(520, 540)
        self.setMinimumSize(440, 420)
        self.setStyleSheet(_STYLESHEET)

        self.assets = []
        self.current_asset = None
        self._approve_checkboxes: list[QCheckBox] = []
        self.show_only_available = False  # filter state

        self._build_ui()
        self.refresh_data()

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
        
        # Filter checkbox
        self.filter_checkbox = QCheckBox("Show only available")
        self.filter_checkbox.setFont(QFont("Segoe UI", 9))
        self.filter_checkbox.stateChanged.connect(self.refresh_data)
        
        # Preset counters
        self.preset_label = QLabel()
        self.preset_label.setFont(QFont("Segoe UI", 9))
        
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(90)
        refresh_btn.clicked.connect(self.refresh_data)
        
        dash.addWidget(self.dashboard_label)
        dash.addStretch()
        dash.addWidget(self.preset_label)
        dash.addWidget(self.filter_checkbox)
        dash.addWidget(refresh_btn)
        root.addLayout(dash)

        # Splitter: table (top) + inspector (bottom)
        splitter = QSplitter(Qt.Orientation.Vertical)
        root.addWidget(splitter, stretch=1)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(8)  # added "Approved" column
        self.table.setHorizontalHeaderLabels(
            ["ID", "Mode", "Duration", "Video", "Approved", "YT", "TT", "IG/FB"]
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
        form.addRow("Title:", self.title_edit)
        form.addRow("Desc:", self.desc_edit)
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
        self.upload_igfb_btn = QPushButton("Upload to IG/FB")
        self.upload_igfb_btn.clicked.connect(self.upload_to_instagram_facebook)
        self.upload_igfb_btn.setEnabled(False)
        btn_row.addStretch()
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.upload_btn)
        btn_row.addWidget(self.upload_tt_btn)
        btn_row.addWidget(self.upload_igfb_btn)
        insp_layout.addLayout(btn_row)

        splitter.addWidget(inspector)
        splitter.setStretchFactor(0, 3)   # table gets more room
        splitter.setStretchFactor(1, 1)

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
            f"IG Uploaded: {stats['ig_uploaded']}"
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
        cb.setChecked(checked)
        cb.stateChanged.connect(lambda _state, a=asset: self._on_approve_toggled(a))
        container = QWidget()
        lay = QHBoxLayout(container)
        lay.addWidget(cb)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setContentsMargins(0, 0, 0, 0)
        return container

    def _on_approve_toggled(self, asset):
        """When an asset's Approved checkbox changes, update upload button if it's the selected asset."""
        if self.current_asset and asset is self.current_asset:
            self.update_upload_button_state()

    def populate_table(self):
        # Filter assets based on checkbox state
        if self.filter_checkbox.isChecked():
            # Show only available: approved but not uploaded to YouTube
            filtered_assets = []
            for asset in self.assets:
                yt_status = asset.upload_status.get("youtube", {})
                approved = yt_status.get("approved", False)
                uploaded = yt_status.get("uploaded", False)
                if approved and not uploaded:
                    filtered_assets.append(asset)
            display_assets = filtered_assets
        else:
            display_assets = self.assets

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
            approved = yt_status.get("approved", False)
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

    # ================= INSPECTOR =================

    def load_inspector(self, row, _):
        # Get the asset from the filtered display list
        if self.filter_checkbox.isChecked():
            filtered_assets = []
            for asset in self.assets:
                yt_status = asset.upload_status.get("youtube", {})
                approved = yt_status.get("approved", False)
                uploaded = yt_status.get("uploaded", False)
                if approved and not uploaded:
                    filtered_assets.append(asset)
            if row < len(filtered_assets):
                asset = filtered_assets[row]
            else:
                return
        else:
            if row >= len(self.assets):
                return
            asset = self.assets[row]
        
        self.current_asset = asset
        self._current_row = row

        with open(asset.metadata_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.title_edit.setText(data.get("title", ""))
        self.desc_edit.setText(data.get("description", ""))

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

        # Reflect normalized values in the editor immediately.
        self.title_edit.setText(normalized_title)
        self.desc_edit.setText(normalized_description)

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

    def update_upload_button_state(self):
        if not self.current_asset:
            self.upload_btn.setEnabled(False)
            self.upload_tt_btn.setEnabled(False)
            self.upload_igfb_btn.setEnabled(False)
            return

        row = self._current_row if hasattr(self, "_current_row") else None
        yt_status = self.current_asset.upload_status.get("youtube", {})
        approved = yt_status.get("approved", False)
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
        self.upload_tt_btn.setEnabled(has_video and approved and not tt_uploaded)

        # IG/FB combined
        igfb_status = self.current_asset.upload_status.get("instagram_facebook", {})
        fb_uploaded = bool(igfb_status.get("facebook_video_id"))
        ig_uploaded = bool(igfb_status.get("instagram_media_id"))
        self.upload_igfb_btn.setEnabled(has_video and approved and (not fb_uploaded or not ig_uploaded))

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



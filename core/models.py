from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class FileMetadata:
    filename: str
    size: Optional[int] = None
    path: str = ""
    extra: Dict = field(default_factory=dict)


@dataclass
class VideoAsset:
    id: str
    metadata_path: str
    video_path: str
    video_exists: bool
    production_mode: str
    duration: float
    created_at: str
    script: str
    upload_status: Dict
    error_state: bool = False
    error_message: str = ""
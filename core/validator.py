from typing import List
from .models import FileMetadata


def validate_metadata(items: List[FileMetadata]) -> List[str]:
    """Return a list of validation error messages for the provided items."""
    errors = []
    for i, it in enumerate(items):
        if not it.filename:
            errors.append(f"item[{i}]: missing filename")
        if it.size is None or it.size < 0:
            errors.append(f"item[{i}] {it.filename}: invalid size {it.size}")
    return errors

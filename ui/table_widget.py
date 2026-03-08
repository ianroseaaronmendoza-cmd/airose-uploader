class TableWidget:
    """Simple table view for metadata items."""

    def display(self, items: list):
        print("--- Items ---")
        for it in items:
            print(f"{getattr(it, 'filename', '<unknown>')}\t{getattr(it, 'size', '?')} bytes")
        print("-------------")

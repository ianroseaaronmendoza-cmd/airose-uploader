class InspectorPanel:
    """Shows detailed metadata for a single item."""

    def inspect(self, item):
        print("--- Inspector ---")
        if not item:
            print("No item selected")
            return
        for k, v in getattr(item, "__dict__", {}).items():
            print(f"{k}: {v}")
        print("------------------")

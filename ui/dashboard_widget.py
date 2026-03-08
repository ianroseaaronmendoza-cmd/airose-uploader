class DashboardWidget:
    """Displays summary statistics."""

    def display(self, stats: dict):
        print("--- Dashboard ---")
        for k, v in stats.items():
            print(f"{k}: {v}")
        print("-----------------")

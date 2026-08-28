import json
import os
from collections import Counter
from datetime import datetime, timezone

class Profiler:
    def __init__(self, records: list):
        self.records = records

    def profile(self) -> dict:
        """Extract statistical distributions from loaded transactions."""
        if not self.records:
            return {}

        amounts = [float(r.amount) for r in self.records]
        avg_amount = sum(amounts) / len(amounts)
        min_amount = min(amounts)
        max_amount = max(amounts)

        methods = [r.payment_method for r in self.records]
        method_counts = Counter(methods)
        total_methods = len(methods)
        method_ratios = {k: v / total_methods for k, v in method_counts.items()}

        return {
            "average_amount": avg_amount,
            "min_amount": min_amount,
            "max_amount": max_amount,
            "payment_method_ratios": method_ratios,
            "calibrated_on": datetime.now(timezone.utc).isoformat()
        }

    def save_profile(self, target_path: str):
        """Serialize profile parameters to JSON."""
        profile_data = self.profile()
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, indent=4)

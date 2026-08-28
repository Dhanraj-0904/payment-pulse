import os
import csv
import random

class CSVLoader:
    def __init__(self, filepath: str):
        self.filepath = filepath

    def load(self) -> list[dict]:
        """Load rows from the target CSV file, creating a mock dataset if missing."""
        if not os.path.exists(self.filepath):
            self._write_mock_paysim()
        
        records = []
        with open(self.filepath, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(row)
        return records

    def _write_mock_paysim(self):
        """Create a mock PaySim CSV file dynamically to calibrate transaction structures."""
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        with open(self.filepath, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "step", "type", "amount", "nameOrig", "oldbalanceOrg", "newbalanceOrig",
                "nameDest", "oldbalanceDest", "newbalanceDest", "isFraud", "isFlaggedFraud"
            ])
            # Generate 100 mock PaySim transaction rows
            types = ["PAYMENT", "TRANSFER", "CASH_OUT", "DEBIT", "CASH_IN"]
            random.seed(42)  # For deterministic generation
            for i in range(100):
                writer.writerow([
                    str(i // 10 + 1),
                    random.choice(types),
                    str(round(random.uniform(500.0, 80000.0), 2)),
                    f"C{i:05d}",
                    "100000.00",
                    "90000.00",
                    f"M{i:05d}",
                    "0.00",
                    "10000.00",
                    "1" if i % 25 == 0 else "0",
                    "0"
                ])

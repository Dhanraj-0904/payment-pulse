from decimal import Decimal
from datetime import datetime, timezone
from simulator.schema import TransactionRecord

class Normalizer:
    @staticmethod
    def normalize_row(row: dict) -> TransactionRecord:
        """Map raw PaySim CSV columns to our internal TransactionRecord representation."""
        paysim_type = row.get("type", "PAYMENT")
        payment_method = {
            "PAYMENT": "UPI",
            "TRANSFER": "CARD",
            "DEBIT": "NETBANKING",
            "CASH_OUT": "WALLET",
            "CASH_IN": "UPI"
        }.get(paysim_type, "UPI")

        # Map simulated bank dynamically based on nameOrig hash
        orig = row.get("nameOrig", "C00000")
        bank = "HDFC Bank"
        if len(orig) > 1 and orig[1].isdigit():
            idx = int(orig[1]) % 5
            bank = ["HDFC Bank", "ICICI Bank", "SBI", "Axis Bank", "Kotak Mahindra Bank"][idx]

        return TransactionRecord(
            transaction_id=f"tx_{row.get('nameOrig', 'UNK')}",
            timestamp=datetime.now(timezone.utc),
            amount=Decimal(row.get("amount", "0.00")),
            currency="INR",
            payment_method=payment_method,
            bank=bank,
            gateway="gateway_alpha",
            merchant_id=row.get("nameDest", "merchant_retail_001"),
            status="SUCCESS",
            error_code=None,
            latency_ms=250,
            location="Pune",
            network_type="4G",
            device_type="MOBILE",
            incident_id=None
        )

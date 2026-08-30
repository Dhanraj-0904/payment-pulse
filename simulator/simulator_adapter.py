import uuid
import random
from dataclasses import replace
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any, Optional

from simulator.environment import StatefulSimulator, VALID_GATEWAYS
from simulator.schema import TransactionRecord
from simulator.injector import _matches, _intensity
from agent.events import PaymentEvent
from agent.event_bus import event_bus

class SimulatorAdapter:
    def __init__(self, simulator: StatefulSimulator):
        self.simulator = simulator
        self.pending_payments: dict[str, TransactionRecord] = {}

    def create_payment(
        self,
        amount: float,
        currency: str,
        payment_method: str,
        bank: str,
        merchant: str
    ) -> dict[str, Any]:
        """Initiate payment and determine the routing gateway based on simulator state."""
        tx_id = f"txn_{uuid.uuid4().hex[:12].upper()}"
        
        # Select a default gateway
        gateway = random.choice(list(VALID_GATEWAYS))
        
        # Apply active routing / reduction actions from the simulator state
        for action in self.simulator.active_actions:
            act_type = action.get("action_type")
            params = action.get("parameters", {})
            
            if act_type == "ROUTE_TRAFFIC":
                src = params.get("source_gateway")
                dst = params.get("destination_gateway")
                bank_filter = params.get("affected_bank")
                method_filter = params.get("affected_payment_method")
                pct = float(params.get("traffic_percentage", 100.0))
                
                if gateway == src:
                    if (bank_filter is None or bank == bank_filter) and \
                       (method_filter is None or payment_method == method_filter):
                        if random.random() * 100.0 < pct:
                            gateway = dst
                            
            elif act_type == "REDUCE_GATEWAY_TRAFFIC":
                gw = params.get("gateway")
                pct = float(params.get("traffic_percentage", 100.0))
                
                if gateway == gw:
                    if random.random() * 100.0 < pct:
                        others = [g for g in VALID_GATEWAYS if g != gw]
                        if others:
                            gateway = random.choice(others)

        # Create record using the current simulator simulation time
        record = TransactionRecord(
            transaction_id=tx_id,
            timestamp=self.simulator.simulation_time,
            amount=Decimal(str(amount)),
            currency=currency,
            payment_method=payment_method,
            bank=bank,
            gateway=gateway,
            merchant_id=merchant,
            status="PROCESSING",
            error_code=None,
            latency_ms=random.randint(150, 450),
            location="Pune",
            network_type="4G",
            device_type="MOBILE",
            incident_id=None
        )
        
        self.pending_payments[tx_id] = record
        
        # Publish Initiated Event
        init_evt = PaymentEvent(
            event_type="PAYMENT_INITIATED",
            transaction_id=tx_id,
            amount=amount,
            currency=currency,
            payment_method=payment_method,
            bank=bank,
            gateway=gateway,
            merchant=merchant,
            status="INITIATED",
            metadata={"sim_time": self.simulator.simulation_time.isoformat()}
        )
        event_bus.publish(init_evt)

        # Publish Processing Event
        proc_evt = PaymentEvent(
            event_type="PAYMENT_PROCESSING",
            transaction_id=tx_id,
            amount=amount,
            currency=currency,
            payment_method=payment_method,
            bank=bank,
            gateway=gateway,
            merchant=merchant,
            status="PROCESSING",
            metadata={"sim_time": self.simulator.simulation_time.isoformat()}
        )
        event_bus.publish(proc_evt)

        return {
            "transaction_id": tx_id,
            "gateway": gateway,
            "status": "PROCESSING"
        }

    def process_payment(self, transaction_id: str) -> dict[str, Any]:
        """Apply active incidents/policies to execute the transaction and return final status."""
        record = self.pending_payments.pop(transaction_id, None)
        if not record:
            return {
                "transaction_id": transaction_id,
                "status": "FAILED",
                "reason": "Payment not found or already processed"
            }

        # 1. Apply active disablement / rate-limiting actions
        for action in self.simulator.active_actions:
            act_type = action.get("action_type")
            params = action.get("parameters", {})
            
            if act_type == "DISABLE_PAYMENT_METHOD":
                method = params.get("payment_method")
                if record.payment_method == method:
                    record = replace(
                        record,
                        status="FAILED",
                        error_code="BANK_DECLINED"
                    )
                    
            elif act_type == "RATE_LIMIT_MERCHANT":
                merch = params.get("merchant")
                pct = float(params.get("traffic_percentage", 100.0))
                if record.merchant_id == merch:
                    if random.random() * 100.0 < pct:
                        record = replace(
                            record,
                            status="FAILED",
                            error_code="BANK_DECLINED"
                        )

        # 2. Apply active incidents if not already failed by policy
        if record.status != "FAILED":
            for inc in self.simulator.incidents_config:
                # Check if the incident is active in the simulator's timeline
                if inc.start_time <= self.simulator.simulation_time < inc.recovery_end_time:
                    if _matches(record, inc):
                        intensity = _intensity(record, inc)
                        if intensity > 0.0:
                            # Standard simulation degradation probability check
                            excess_failure_probability = min(0.90, 0.035 * (inc.failure_rate_multiplier - 1) * intensity)
                            becomes_failed = random.random() < excess_failure_probability
                            
                            status = "FAILED" if becomes_failed else record.status
                            error_code = record.error_code
                            if becomes_failed:
                                error_code = inc.affected_error_code
                            elif status == "FAILED" and random.random() < 0.82 * intensity:
                                error_code = inc.affected_error_code
                            
                            latency_multiplier = 1 + (inc.latency_multiplier - 1) * intensity
                            latency_ms = max(record.latency_ms, int(round(record.latency_ms * latency_multiplier * random.uniform(0.90, 1.10))))
                            
                            record = replace(
                                record,
                                status=status,
                                error_code=error_code,
                                latency_ms=latency_ms,
                                incident_id=inc.incident_id
                            )

        # 3. Finalize success state if not failed
        if record.status == "PROCESSING":
            record = replace(record, status="SUCCESS")

        # Accumulate transaction in the simulator's active step collection
        self.simulator.last_step_transactions.append(record)

        # Publish Outcome Event
        evt_type = "PAYMENT_SUCCESS" if record.status == "SUCCESS" else "PAYMENT_FAILED"
        outcome_evt = PaymentEvent(
            event_type=evt_type,
            transaction_id=record.transaction_id,
            amount=float(record.amount),
            currency=record.currency,
            payment_method=record.payment_method,
            bank=record.bank,
            gateway=record.gateway,
            merchant=record.merchant_id,
            status=record.status,
            metadata={
                "error_code": record.error_code,
                "latency_ms": record.latency_ms,
                "incident_id": record.incident_id,
                "sim_time": record.timestamp.isoformat()
            }
        )
        event_bus.publish(outcome_evt)

        return {
            "transaction_id": record.transaction_id,
            "status": record.status,
            "error_code": record.error_code,
            "latency_ms": record.latency_ms
        }

    def get_payment_status(self, transaction_id: str) -> Optional[dict[str, Any]]:
        """Query status of a pending or processed transaction."""
        if transaction_id in self.pending_payments:
            rec = self.pending_payments[transaction_id]
            return {
                "transaction_id": transaction_id,
                "status": rec.status,
                "gateway": rec.gateway
            }
        return None

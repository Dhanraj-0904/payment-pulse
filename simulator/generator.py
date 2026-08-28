"""Reproducible, correlated normal-baseline transaction generator."""

import math
import random
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterator

from simulator.config import GeneratorConfig
from simulator.profiles import (
    BANKS,
    DEVICE_TYPES,
    GATEWAYS,
    LOCATIONS,
    MERCHANTS,
    NETWORK_TYPES,
    PAYMENT_METHODS,
    BaselineProfile,
)
from simulator.schema import METHOD_ERRORS, TransactionRecord


def _weighted_method(rng: random.Random):
    profiles, weights, amounts, variations = zip(*PAYMENT_METHODS)
    selected = rng.choices(range(len(profiles)), weights=weights, k=1)[0]
    return profiles[selected], amounts[selected], variations[selected]


def _success_probability(*profiles: BaselineProfile) -> float:
    """Combine small baseline deviations while keeping normal data stable."""
    probability = 0.965
    for profile in profiles:
        probability += (profile.success_rate - 0.965) * 0.55
    return max(0.90, min(0.992, probability))


def _failure_error(rng: random.Random, payment_method: str) -> str:
    weights = {
        "UPI": ("TIMEOUT", "NETWORK_ERROR", "BANK_DECLINED", "AUTH_FAILED", "UNKNOWN"),
        "CARD": ("CARD_DECLINED", "BANK_DECLINED", "AUTH_FAILED", "TIMEOUT", "NETWORK_ERROR", "UNKNOWN"),
        "NETBANKING": ("TIMEOUT", "BANK_DECLINED", "NETWORK_ERROR", "AUTH_FAILED", "UNKNOWN"),
        "WALLET": ("AUTH_FAILED", "NETWORK_ERROR", "TIMEOUT", "UNKNOWN"),
    }[payment_method]
    error = rng.choice(weights)
    assert error in METHOD_ERRORS[payment_method]
    return error


def _latency(rng: random.Random, baseline_ms: float, failure: bool, error_code: str | None) -> int:
    latency = rng.lognormvariate(math.log(baseline_ms), 0.38)
    if failure:
        latency *= 1.35
    if error_code == "TIMEOUT":
        latency = max(latency * rng.uniform(2.5, 4.5), 5000)
    elif error_code == "NETWORK_ERROR":
        latency *= rng.uniform(1.4, 2.2)
    elif error_code in {"CARD_DECLINED", "BANK_DECLINED", "AUTH_FAILED"}:
        latency *= rng.uniform(0.65, 1.15)
    return max(1, int(round(latency)))


def _amount(rng: random.Random, mean: float, variation: float, merchant_multiplier: float) -> Decimal:
    value = max(10.0, rng.lognormvariate(math.log(mean * merchant_multiplier), variation))
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def generate_transactions(config: GeneratorConfig) -> Iterator[TransactionRecord]:
    """Yield deterministic normal-baseline transactions without incident injection."""
    rng = random.Random(config.random_seed)
    for index in range(config.transaction_count):
        method, amount_mean, amount_variation = _weighted_method(rng)
        bank = rng.choice(BANKS)
        gateway = rng.choice(GATEWAYS)
        merchant, merchant_multiplier = rng.choice(MERCHANTS)
        failed = rng.random() >= _success_probability(method, bank, gateway, merchant)
        error_code = _failure_error(rng, method.name) if failed else None
        latency_base = 900 * method.latency_multiplier * bank.latency_multiplier * gateway.latency_multiplier
        yield TransactionRecord(
            transaction_id=f"txn_{config.random_seed}_{index:09d}",
            timestamp=config.start_timestamp
            + timedelta(seconds=index * config.transaction_frequency_seconds),
            merchant_id=merchant.name,
            amount=_amount(rng, amount_mean, amount_variation, merchant_multiplier),
            currency="INR",
            payment_method=method.name,
            bank=bank.name,
            gateway=gateway.name,
            device_type=rng.choices(DEVICE_TYPES, weights=(0.58, 0.27, 0.15), k=1)[0],
            network_type=rng.choices(NETWORK_TYPES, weights=(0.35, 0.25, 0.25, 0.15), k=1)[0],
            location=rng.choice(LOCATIONS),
            latency_ms=_latency(rng, latency_base, failed, error_code),
            status="FAILED" if failed else "SUCCESS",
            error_code=error_code,
            incident_id=None,
        )

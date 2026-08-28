"""Stable baseline profiles for realistic, incident-ready synthetic data."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BaselineProfile:
    name: str
    success_rate: float
    latency_multiplier: float


BANKS = (
    BaselineProfile("Axis Bank", 0.965, 0.98),
    BaselineProfile("HDFC Bank", 0.972, 0.94),
    BaselineProfile("ICICI Bank", 0.969, 0.97),
    BaselineProfile("SBI", 0.957, 1.08),
    BaselineProfile("Kotak Mahindra Bank", 0.962, 1.02),
)

PAYMENT_METHODS = (
    (BaselineProfile("UPI", 0.966, 0.85), 0.45, 700.0, 0.55),
    (BaselineProfile("CARD", 0.958, 1.00), 0.30, 1800.0, 0.65),
    (BaselineProfile("NETBANKING", 0.950, 1.30), 0.15, 2400.0, 0.70),
    (BaselineProfile("WALLET", 0.975, 0.75), 0.10, 550.0, 0.50),
)

GATEWAYS = (
    BaselineProfile("gateway_alpha", 0.970, 0.93),
    BaselineProfile("gateway_beta", 0.962, 1.00),
    BaselineProfile("gateway_gamma", 0.956, 1.12),
)

MERCHANTS = (
    (BaselineProfile("merchant_retail_001", 0.972, 0.96), 1.00),
    (BaselineProfile("merchant_marketplace_002", 0.963, 1.04), 1.35),
    (BaselineProfile("merchant_subscription_003", 0.975, 0.90), 0.75),
    (BaselineProfile("merchant_travel_004", 0.955, 1.14), 2.20),
    (BaselineProfile("merchant_food_005", 0.968, 0.92), 0.65),
)

DEVICE_TYPES = ("ANDROID", "IOS", "WEB")
NETWORK_TYPES = ("4G", "5G", "WIFI", "BROADBAND")
LOCATIONS = ("Bengaluru", "Delhi", "Mumbai", "Hyderabad", "Pune", "Chennai")

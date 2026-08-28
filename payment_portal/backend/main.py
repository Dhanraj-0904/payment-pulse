import os
import urllib.request
import urllib.error
import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="Payment Pulse Customer Portal Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PAYMENT_PULSE_URL = os.getenv("PAYMENT_PULSE_URL", "http://localhost:8000")

class CheckoutRequest(BaseModel):
    amount: float
    payment_method: str
    bank: str
    merchant: str

class ExecuteRequest(BaseModel):
    transaction_id: str

PRODUCTS = [
    {
        "product_id": "prod_laptop_001",
        "name": "Developer Pro Laptop",
        "price": 74999.00,
        "description": "High performance developer laptop with 32GB RAM and 1TB SSD.",
        "image": "https://images.unsplash.com/photo-1517694712202-14dd9538aa97?w=500&auto=format&fit=crop&q=60"
    },
    {
        "product_id": "prod_headphones_002",
        "name": "Noise Cancelling Headphones",
        "price": 14999.00,
        "description": "Premium active noise cancelling headphones with 30-hour battery life.",
        "image": "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=500&auto=format&fit=crop&q=60"
    },
    {
        "product_id": "prod_phone_003",
        "name": "Flagship Smartphone",
        "price": 59999.00,
        "description": "Ultimate smartphone with custom AI chip and triple lens camera system.",
        "image": "https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=500&auto=format&fit=crop&q=60"
    },
    {
        "product_id": "prod_watch_004",
        "name": "Smart Watch Series S",
        "price": 24999.00,
        "description": "Advanced health tracking smartwatch with always-on retina display.",
        "image": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=500&auto=format&fit=crop&q=60"
    }
]

@app.get("/api/products")
def get_products():
    return PRODUCTS

@app.post("/api/checkout")
def checkout(req: CheckoutRequest):
    # Forward the request to Payment Pulse backend
    url = f"{PAYMENT_PULSE_URL}/api/payments/initiate"
    data = json.dumps({
        "amount": req.amount,
        "payment_method": req.payment_method,
        "bank": req.bank,
        "merchant": req.merchant
    }).encode("utf-8")
    
    headers = {"Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        raise HTTPException(status_code=e.code, detail=f"Payment Pulse Error: {err_msg}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Connection Error to Payment Pulse: {str(e)}")

@app.post("/api/payment/execute")
def execute_payment(req: ExecuteRequest):
    # Forward execution to Payment Pulse backend
    url = f"{PAYMENT_PULSE_URL}/api/payments/process/{req.transaction_id}"
    request = urllib.request.Request(url, headers={}, method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        raise HTTPException(status_code=e.code, detail=f"Payment Pulse Error: {err_msg}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Connection Error to Payment Pulse: {str(e)}")

# Mount static files dynamically
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

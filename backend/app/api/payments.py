from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.core.simulator_adapter import get_simulator_adapter
from simulator.simulator_adapter import SimulatorAdapter

router = APIRouter(prefix="/api/payments", tags=["payments"])

class InitiateRequest(BaseModel):
    amount: float
    payment_method: str
    bank: str
    merchant: str

@router.post("/initiate")
def initiate_payment(req: InitiateRequest, adapter: SimulatorAdapter = Depends(get_simulator_adapter)):
    try:
        return adapter.create_payment(
            amount=req.amount,
            currency="INR",
            payment_method=req.payment_method,
            bank=req.bank,
            merchant=req.merchant
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/process/{transaction_id}")
def process_payment(transaction_id: str, adapter: SimulatorAdapter = Depends(get_simulator_adapter)):
    try:
        res = adapter.process_payment(transaction_id)
        if "reason" in res and res["status"] == "FAILED" and "not found" in res["reason"]:
            raise HTTPException(status_code=404, detail=res["reason"])
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

import os
import time
from typing import Optional, Literal, Dict, Any

import jwt
from dateutil.relativedelta import relativedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests

from database import create_document, get_documents, db
from schemas import User, License

APP_NAME = "Toolkit Converter"
JWT_ALG = os.getenv("ENTITLEMENT_JWT_ALG", "HS256")
JWT_SECRET = os.getenv("ENTITLEMENT_JWT_SECRET", "dev-secret-change-me")
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "")
DODO_API_BASE = os.getenv("DODO_API_BASE", "https://api.dodopayments.com")
DODO_API_KEY = os.getenv("DODO_API_KEY", "")

app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConvertRequest(BaseModel):
    value: float
    from_unit: str
    to_unit: str

class LicenseVerifyRequest(BaseModel):
    license_key: str
    user_email: Optional[str] = None

class EntitlementTokenResponse(BaseModel):
    entitlement_token: str
    expires_at: int
    plan: Literal["free", "pro"]

# ---- Conversion Logic ----
# Free units map (basic)
FREE_UNITS: Dict[str, float] = {
    # length (meters base)
    "mm": 0.001,
    "cm": 0.01,
    "m": 1.0,
    "km": 1000.0,
    # weight (grams base)
    "mg": 0.001,
    "g": 1.0,
    "kg": 1000.0,
    # temperature supported separately in free
}

# Pro units map (enhanced), includes time, area, volume and more uncommon units
PRO_UNITS: Dict[str, Dict[str, float]] = {
    "length": {
        "in": 0.0254,
        "ft": 0.3048,
        "yd": 0.9144,
        "mi": 1609.344,
        "nm": 1e-9,
        "um": 1e-6,
    },
    "area": {
        "m2": 1.0,
        "cm2": 1e-4,
        "km2": 1e6,
        "ft2": 0.09290304,
        "acre": 4046.8564224,
    },
    "volume": {
        "ml": 1e-6,
        "l": 1e-3,
        "m3": 1.0,
        "ft3": 0.028316846592,
        "gal": 0.003785411784,
    },
    "weight": {
        "lb": 453.59237,
        "oz": 28.349523125,
        "ton": 1_000_000.0,
    },
    "time": {
        "ms": 0.001,
        "s": 1.0,
        "min": 60.0,
        "h": 3600.0,
        "day": 86400.0,
    },
}

TEMPS = {"C", "F", "K"}

def convert_temperature(value: float, from_unit: str, to_unit: str) -> float:
    f = from_unit.upper()
    t = to_unit.upper()
    if f == t:
        return value
    # Convert to Celsius first
    if f == "C":
        c = value
    elif f == "F":
        c = (value - 32) * 5/9
    elif f == "K":
        c = value - 273.15
    else:
        raise HTTPException(status_code=400, detail="Unsupported temperature unit")

    if t == "C":
        return c
    if t == "F":
        return c * 9/5 + 32
    if t == "K":
        return c + 273.15
    raise HTTPException(status_code=400, detail="Unsupported temperature unit")


def generate_entitlement(email: Optional[str], plan: Literal["free", "pro"], license_id: Optional[str] = None, hours: int = 24) -> EntitlementTokenResponse:
    now = int(time.time())
    exp = now + hours * 3600
    payload = {
        "sub": email or "anonymous",
        "plan": plan,
        "license_id": license_id,
        "iat": now,
        "exp": exp,
        "scope": {"converter": plan},
        "ver": 1,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
    return EntitlementTokenResponse(entitlement_token=token, expires_at=exp, plan=plan)


@app.get("/")
def root():
    return {"message": f"{APP_NAME} backend ready"}


@app.post("/api/convert")
def convert(req: ConvertRequest, authorization: Optional[str] = None):
    # Determine plan from entitlement token (if provided)
    plan = "free"
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        try:
            claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
            if claims.get("plan") == "pro":
                plan = "pro"
        except Exception:
            plan = "free"

    fu = req.from_unit.lower()
    tu = req.to_unit.lower()

    # Temperature conversions available for free
    if fu.upper() in TEMPS and tu.upper() in TEMPS:
        return {"result": convert_temperature(req.value, req.from_unit, req.to_unit), "plan": plan}

    # Basic metric conversions (length and weight) for free
    if fu in FREE_UNITS and tu in FREE_UNITS:
        # determine group: length or weight by base
        # Both length
        if all(u in {"mm","cm","m","km"} for u in [fu, tu]):
            base_factor = 1.0
            result = req.value * (FREE_UNITS[fu] / FREE_UNITS[tu])
            return {"result": result, "plan": plan}
        # Both weight
        if all(u in {"mg","g","kg"} for u in [fu, tu]):
            result = req.value * (FREE_UNITS[fu] / FREE_UNITS[tu])
            return {"result": result, "plan": plan}

    # Pro-only conversions (imperial, area, volume, time, etc.)
    if plan != "pro":
        raise HTTPException(status_code=402, detail="Pro required for this conversion. Upgrade for $3/month or $30/year.")

    # Check in pro maps
    # Length with imperial/si mixed
    if fu in PRO_UNITS["length"] or tu in PRO_UNITS["length"] or fu in {"mm","cm","m","km"} or tu in {"mm","cm","m","km"}:
        # Convert any supported length to meters then to target
        def to_m(u: str) -> float:
            if u in FREE_UNITS and u in {"mm","cm","m","km"}:
                return FREE_UNITS[u]
            return PRO_UNITS["length"].get(u, None)
        f_factor = to_m(fu)
        t_factor = to_m(tu)
        if f_factor is not None and t_factor is not None:
            result = req.value * (f_factor / t_factor)
            return {"result": result, "plan": plan}

    # Area
    if fu in PRO_UNITS["area"] and tu in PRO_UNITS["area"]:
        result = req.value * (PRO_UNITS["area"][fu] / PRO_UNITS["area"][tu])
        return {"result": result, "plan": plan}

    # Volume
    if fu in PRO_UNITS["volume"] and tu in PRO_UNITS["volume"]:
        result = req.value * (PRO_UNITS["volume"][fu] / PRO_UNITS["volume"][tu])
        return {"result": result, "plan": plan}

    # Weight extended
    if fu in PRO_UNITS["weight"] or tu in PRO_UNITS["weight"] or fu in {"mg","g","kg"} or tu in {"mg","g","kg"}:
        def to_g(u: str) -> Optional[float]:
            if u in {"mg","g","kg"}:
                return FREE_UNITS[u]
            return PRO_UNITS["weight"].get(u, None)
        f_factor = to_g(fu)
        t_factor = to_g(tu)
        if f_factor is not None and t_factor is not None:
            result = req.value * (f_factor / t_factor)
            return {"result": result, "plan": plan}

    # Time
    if fu in PRO_UNITS["time"] and tu in PRO_UNITS["time"]:
        result = req.value * (PRO_UNITS["time"][fu] / PRO_UNITS["time"][tu])
        return {"result": result, "plan": plan}

    raise HTTPException(status_code=400, detail="Unsupported conversion units")


@app.post("/api/license/verify", response_model=EntitlementTokenResponse)
def verify_license(req: LicenseVerifyRequest):
    if not DODO_API_KEY:
        raise HTTPException(status_code=500, detail="Dodo Payments API key not configured")

    # Verify license with Dodo Payments (pseudo: check subscription status by license key)
    # Dodo docs expose license/subscription verification; here we call a generic endpoint placeholder.
    # Replace with actual API path if different.
    try:
        url = f"{DODO_API_BASE}/v1/licenses/verify"
        headers = {"Authorization": f"Bearer {DODO_API_KEY}", "Content-Type": "application/json"}
        payload = {"license_key": req.license_key}
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail="Invalid license key")
        data = r.json()
        status = data.get("status")
        plan = data.get("plan", "pro") if status == "active" else "free"
        if status != "active":
            raise HTTPException(status_code=400, detail="License not active")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Dodo verification failed: {str(e)[:80]}")

    email = req.user_email

    # Persist license metadata
    try:
        create_document("license", License(user_email=email or "anonymous", license_key=req.license_key, plan="pro-month" if plan=="pro" else "free", status="active").model_dump())
    except Exception:
        pass

    # Return entitlement token for 24h
    return generate_entitlement(email, "pro", license_id=req.license_key, hours=24)


class RefreshRequest(BaseModel):
    entitlement_token: str

@app.post("/api/entitlement/refresh", response_model=EntitlementTokenResponse)
def refresh(req: RefreshRequest):
    try:
        claims = jwt.decode(req.entitlement_token, JWT_SECRET, algorithms=[JWT_ALG], options={"verify_exp": False})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid token")

    email = claims.get("sub")
    if not DODO_API_KEY:
        # If no key, simply reissue as-is for dev
        return generate_entitlement(email, claims.get("plan", "free"), claims.get("license_id"), hours=24)

    # Optional: Check with Dodo to ensure subscription still active for the license
    license_id = claims.get("license_id")
    if not license_id or license_id == "anonymous":
        return generate_entitlement(email, "free", hours=24)

    try:
        url = f"{DODO_API_BASE}/v1/licenses/status/{license_id}"
        headers = {"Authorization": f"Bearer {DODO_API_KEY}"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return generate_entitlement(email, "free", hours=24)
        data = r.json()
        if data.get("status") == "active":
            return generate_entitlement(email, "pro", license_id=license_id, hours=24)
        return generate_entitlement(email, "free", hours=24)
    except Exception:
        # If Dodo unreachable, extend current plan for resilience but cap at 24h
        plan = claims.get("plan", "free")
        return generate_entitlement(email, plan, license_id=license_id, hours=24)


@app.get("/api/pricing")
def pricing():
    return {
        "currency": "USD",
        "monthly": {"price": 3, "interval": "month"},
        "yearly": {"price": 30, "interval": "year"},
        "provider": "Dodo",
    }


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

import os
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
from datetime import datetime, timezone
from bson import ObjectId

# Database
from database import db, create_document, get_documents

app = FastAPI(title="NavKar Jewellery API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------- Helpers -------------

def to_str_id(doc: dict) -> dict:
    if not doc:
        return doc
    d = {**doc}
    if d.get("_id"):
        d["id"] = str(d.pop("_id"))
    # convert datetime to iso
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.astimezone(timezone.utc).isoformat()
    return d

class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return v
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)

# ------------- Schemas -------------

class ProductIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    price: float = Field(..., ge=0)
    category: str = Field(..., min_length=2, max_length=60)
    image_url: Optional[str] = Field(None, description="Public image URL")
    in_stock: bool = True

class ProductOut(ProductIn):
    id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class CartItem(BaseModel):
    product_id: str
    quantity: int = Field(..., ge=1, le=10)

class CustomerInfo(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    address: str
    city: str
    zip_code: str
    country: str

class OrderIn(BaseModel):
    items: List[CartItem]
    customer: CustomerInfo
    payment_method: str = Field(..., description="e.g., card, upi, cod")

class OrderOut(BaseModel):
    id: str
    items: List[dict]
    total_amount: float
    status: str
    payment_method: str
    customer: dict
    created_at: datetime

# ------------- Auth (simple admin token) -------------

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin123")

def require_admin(x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")

# ------------- Routes -------------

@app.get("/")
def read_root():
    return {"message": "NavKar Jewellery API running"}

@app.get("/api/hello")
def hello():
    return {"message": "Welcome to NavKar Jewellery"}

# Products
@app.get("/api/products", response_model=List[ProductOut])
def list_products(category: Optional[str] = None, q: Optional[str] = None):
    filter_dict = {}
    if category:
        filter_dict["category"] = category
    if q:
        filter_dict["name"] = {"$regex": q, "$options": "i"}
    docs = list(db["product"].find(filter_dict).sort("created_at", -1))
    return [ProductOut(**to_str_id(d)) for d in docs]

@app.get("/api/products/{product_id}", response_model=ProductOut)
def get_product(product_id: str):
    doc = db["product"].find_one({"_id": PyObjectId.validate(product_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Product not found")
    return ProductOut(**to_str_id(doc))

@app.post("/api/products", response_model=str, dependencies=[Depends(require_admin)])
def create_product(payload: ProductIn):
    data = payload.model_dump()
    new_id = create_document("product", data)
    return new_id

@app.put("/api/products/{product_id}", dependencies=[Depends(require_admin)])
def update_product(product_id: str, payload: ProductIn):
    oid = PyObjectId.validate(product_id)
    data = payload.model_dump()
    data["updated_at"] = datetime.now(timezone.utc)
    res = db["product"].update_one({"_id": oid}, {"$set": data})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    doc = db["product"].find_one({"_id": oid})
    return to_str_id(doc)

@app.delete("/api/products/{product_id}", dependencies=[Depends(require_admin)])
def delete_product(product_id: str):
    oid = PyObjectId.validate(product_id)
    res = db["product"].delete_one({"_id": oid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"deleted": True}

# Orders
@app.post("/api/orders", response_model=OrderOut)
def create_order(order: OrderIn):
    if not order.items:
        raise HTTPException(status_code=400, detail="Cart is empty")
    # compute total and expand items with product snapshot
    product_ids = [PyObjectId.validate(i.product_id) for i in order.items]
    prods = {str(d["_id"]): d for d in db["product"].find({"_id": {"$in": product_ids}})}
    enriched_items = []
    total = 0.0
    for item in order.items:
        prod = prods.get(item.product_id)
        if not prod:
            raise HTTPException(status_code=400, detail=f"Invalid product {item.product_id}")
        price = float(prod.get("price", 0))
        enriched_items.append({
            "product_id": item.product_id,
            "name": prod.get("name"),
            "price": price,
            "quantity": item.quantity,
            "image_url": prod.get("image_url"),
        })
        total += price * item.quantity
    order_doc = {
        "items": enriched_items,
        "total_amount": round(total, 2),
        "status": "pending",  # pending -> paid -> shipped
        "payment_method": order.payment_method,
        "customer": order.customer.model_dump(),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    inserted_id = db["order"].insert_one(order_doc).inserted_id
    out = db["order"].find_one({"_id": inserted_id})
    out = to_str_id(out)
    return OrderOut(
        id=out["id"],
        items=out["items"],
        total_amount=out["total_amount"],
        status=out["status"],
        payment_method=out["payment_method"],
        customer=out["customer"],
        created_at=datetime.fromisoformat(out["created_at"]) if isinstance(out["created_at"], str) else out["created_at"],
    )

@app.get("/api/orders", dependencies=[Depends(require_admin)])
def list_orders(limit: int = 50):
    docs = list(db["order"].find().sort("created_at", -1).limit(limit))
    return [to_str_id(d) for d in docs]

@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    doc = db["order"].find_one({"_id": PyObjectId.validate(order_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Order not found")
    return to_str_id(doc)

@app.post("/api/orders/{order_id}/mark-paid", dependencies=[Depends(require_admin)])
def mark_order_paid(order_id: str):
    oid = PyObjectId.validate(order_id)
    res = db["order"].update_one({"_id": oid}, {"$set": {"status": "paid", "updated_at": datetime.now(timezone.utc)}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"ok": True}

# Health & DB test
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
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = os.getenv("DATABASE_NAME") or "Unknown"
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
    return response

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

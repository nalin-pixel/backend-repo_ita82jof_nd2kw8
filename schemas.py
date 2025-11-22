from pydantic import BaseModel, Field, EmailStr
from typing import Optional

# Collections will be created automatically when inserting documents

class Product(BaseModel):
    name: str = Field(..., min_length=2, max_length=120, description="Jewellery name")
    description: Optional[str] = Field(None, max_length=2000)
    price: float = Field(..., ge=0)
    category: str = Field(..., description="e.g., Rings, Necklaces, Earrings")
    image_url: Optional[str] = Field(None, description="Public image URL of the item")
    in_stock: bool = Field(True)

class Order(BaseModel):
    items: list = Field(default_factory=list, description="List of items in the order")
    total_amount: float = Field(0, ge=0)
    status: str = Field("pending", description="pending | paid | shipped")
    payment_method: str = Field("card", description="card | upi | cod")
    customer: dict = Field(default_factory=dict)

class AdminUser(BaseModel):
    email: EmailStr
    name: str
    role: str = Field("admin")
    is_active: bool = Field(True)

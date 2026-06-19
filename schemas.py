from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional
from datetime import datetime
from decimal import Decimal

# ============ AUTH SCHEMAS ============
class UserBase(BaseModel):
    email: EmailStr
    full_name: str

class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=72)

class UserResponse(UserBase):
    id: int
    is_active: bool
    date_joined: datetime
    is_2fa_enabled: Optional[bool] = False
    two_factor_secret: Optional[str] = None
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: Optional[str] = None
    token_type: Optional[str] = None
    require_2fa: Optional[bool] = None
    email: Optional[str] = None

class TokenData(BaseModel):
    email: Optional[str] = None

# ============ COMPANY SCHEMAS ============
class CompanyProfileBase(BaseModel):
    name: str
    logo_url: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    tax_number: Optional[str] = None
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc_code: Optional[str] = None

class CompanyProfileCreate(CompanyProfileBase):
    pass

class CompanyProfile(CompanyProfileBase):
    id: int
    class Config:
        from_attributes = True

# ============ INVOICE ITEM SCHEMAS ============
class InvoiceItemBase(BaseModel):
    description: str
    quantity: int
    unit_price: float

class InvoiceItemCreate(InvoiceItemBase):
    pass

class InvoiceItem(InvoiceItemBase):
    id: int
    invoice_id: int
    total_price: float
    class Config:
        from_attributes = True

# ============ CLIENT SCHEMAS ============
class ClientBase(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    address: Optional[str] = None

class ClientCreate(ClientBase):
    pass

class Client(ClientBase):
    id: int
    class Config:
        from_attributes = True

# ============ INVOICE SCHEMAS ============
class InvoiceBase(BaseModel):
    client_id: int
    status: Optional[str] = "Unpaid"
    due_date: Optional[datetime] = None

class InvoiceCreate(InvoiceBase):
    # This allows you to send items along with the invoice in one request
    items: List[InvoiceItemCreate]

class Invoice(InvoiceBase):
    id: int
    invoice_number: str
    subtotal: float
    tax_rate: float
    total_amount: float
    date_created: datetime
    items: List[InvoiceItem] = []
    
    class Config:
        from_attributes = True

# ============ PASSWORD RESET SCHEMAS ============

class ForgotPasswordRequest(BaseModel):
    """Schema for the initial forgot-password request."""
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    """Schema for the final password reset using the token."""
    token: str
    new_password: str = Field(..., min_length=8, max_length=72)

# ============ AGENCY SAFETY SCHEMAS ============
class AgencySafetyCreate(BaseModel):
    image_data: str
    watermark_text: Optional[str] = "DRAFT - DO NOT USE"
    opacity: Optional[int] = 30
    invoice_id: Optional[int] = None

class AgencySafetyResponse(BaseModel):
    id: int
    secure_id: str
    image_data: str
    watermark_text: str
    opacity: int
    invoice_id: Optional[int]
    status: str = "Unpaid"

    class Config:
        from_attributes = True

# ============ 2FA SCHEMAS ============
class TwoFASetupRequest(BaseModel):
    email: EmailStr

class TwoFAVerifyRequest(BaseModel):
    email: EmailStr
    code: str
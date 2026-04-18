from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text, Numeric, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base
import uuid

# ================= USER AUTHENTICATION =================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    date_joined = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Real-world ownership links
    clients = relationship("Client", back_populates="owner")
    invoices = relationship("Invoice", back_populates="owner")
    company_profiles = relationship("CompanyProfile", back_populates="owner")

# ================= COMPANY PROFILE =================
class CompanyProfile(Base):
    __tablename__ = "company_profile"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True) # Multitenancy
    
    name = Column(String(255), default="DK DEVELOPERS")
    logo_url = Column(String(500), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    tax_number = Column(String(100), nullable=True)
    bank_name = Column(String(255), nullable=True)
    account_number = Column(String(100), nullable=True)
    ifsc_code = Column(String(50), nullable=True)

    owner = relationship("User", back_populates="company_profiles")

# ================= CLIENTS =================
class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True) # Multitenancy
    
    name = Column(String(255), nullable=False)
    email = Column(String(255), index=True) # Removed unique=True to allow different owners to have same client email
    phone = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    
    owner = relationship("User", back_populates="clients")
    invoices = relationship("Invoice", back_populates="client")

# ================= INVOICES =================
class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True) # Multitenancy
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    
    invoice_number = Column(String(50), unique=True, nullable=False, 
                            default=lambda: f"INV-{str(uuid.uuid4())[:8].upper()}")
    
    subtotal = Column(Numeric(10, 2), default=0.00)
    tax_rate = Column(Numeric(5, 2), default=0.00)
    total_amount = Column(Numeric(10, 2), default=0.00)
    
    status = Column(String(50), default="Unpaid")
    date_created = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    due_date = Column(DateTime, nullable=True)
    
    owner = relationship("User", back_populates="invoices")
    client = relationship("Client", back_populates="invoices")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")

# ================= INVOICE ITEMS =================
class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    description = Column(String(255), nullable=False)
    quantity = Column(Integer, default=1)
    unit_price = Column(Numeric(10, 2), nullable=False)
    total_price = Column(Numeric(10, 2), nullable=False)
    
    invoice = relationship("Invoice", back_populates="items")
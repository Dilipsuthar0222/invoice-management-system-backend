import os
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List
import models
import schemas
from database import engine, get_db, Base
from datetime import datetime, timedelta
from jose import JWTError, jwt
import bcrypt
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from auth_utils import router as auth_router

# Load environment variables
load_dotenv()

# Initialize Database
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Dk Developers Invoice API", version="2.0.0")

app.include_router(auth_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth Constants from .env
SECRET_KEY = os.getenv("SECRET_KEY", "fallback_secret_for_dev")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Password Helpers
def get_password_hash(password: str):
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(status_code=401, detail="Invalid session")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None: raise credentials_exception
    except JWTError: raise credentials_exception
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None: raise credentials_exception
    return user

# ============ AUTH ROUTES ============

@app.post("/register", response_model=schemas.UserResponse)
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.email == user.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    new_user = models.User(email=user.email, full_name=user.full_name, hashed_password=get_password_hash(user.password))
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.post("/login", response_model=schemas.Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"access_token": create_access_token(data={"sub": user.email}), "token_type": "bearer"}

@app.get("/auth/me", response_model=schemas.UserResponse)
def get_me(current_user: models.User = Depends(get_current_user)):
    return current_user

# ============ COMPANY PROFILE ROUTES ============

@app.get("/company", response_model=schemas.CompanyProfile)
def get_company(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(models.CompanyProfile).filter(models.CompanyProfile.owner_id == current_user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Company profile not found")
    return profile

@app.post("/company", response_model=schemas.CompanyProfile)
def update_company(profile: schemas.CompanyProfileCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    db_profile = db.query(models.CompanyProfile).filter(models.CompanyProfile.owner_id == current_user.id).first()
    profile_data = profile.model_dump()
    profile_data["owner_id"] = current_user.id
    
    if db_profile:
        for key, value in profile_data.items():
            setattr(db_profile, key, value)
    else:
        db_profile = models.CompanyProfile(**profile_data)
        db.add(db_profile)
    
    db.commit()
    db.refresh(db_profile)
    return db_profile

# ============ CLIENT ROUTES ============

@app.get("/clients", response_model=List[schemas.Client])
def get_clients(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(models.Client).filter(models.Client.owner_id == current_user.id).all()

@app.post("/clients")
def create_client(client: schemas.ClientCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Check for duplicate client email under the same owner
    existing_client = db.query(models.Client).filter(
        models.Client.email == client.email, 
        models.Client.owner_id == current_user.id
    ).first()
    
    if existing_client:
        raise HTTPException(status_code=400, detail="A client with this email already exists in your workspace.")

    client_data = client.model_dump()
    client_data["owner_id"] = current_user.id
    db_client = models.Client(**client_data)
    db.add(db_client)
    db.commit()
    db.refresh(db_client)
    return db_client

@app.delete("/clients/{client_id}")
def delete_client(client_id: int, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    db_client = db.query(models.Client).filter(models.Client.id == client_id, models.Client.owner_id == current_user.id).first()
    if not db_client: raise HTTPException(status_code=404, detail="Client not found")
    db.delete(db_client)
    db.commit()
    return {"detail": "Client deleted"}

# ============ INVOICE ROUTES ============

@app.get("/invoices")
def get_invoices(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    results = db.query(
        models.Invoice.id, models.Invoice.invoice_number,
        models.Invoice.total_amount, models.Invoice.status,
        models.Invoice.date_created, models.Client.name.label("client_name")
    ).join(models.Client).filter(models.Invoice.owner_id == current_user.id).all()
    return [dict(row._mapping) for row in results]

@app.post("/invoices", response_model=schemas.Invoice)
def create_invoice(invoice_data: schemas.InvoiceCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Verify client belongs to user
    client = db.query(models.Client).filter(models.Client.id == invoice_data.client_id, models.Client.owner_id == current_user.id).first()
    if not client: raise HTTPException(status_code=403, detail="Unauthorized client access")

    subtotal = sum(i.quantity * i.unit_price for i in invoice_data.items)
    total = subtotal + (subtotal * 18 / 100)
    db_invoice = models.Invoice(owner_id=current_user.id, client_id=invoice_data.client_id, subtotal=subtotal, tax_rate=18.0, total_amount=total, status="Unpaid")
    db.add(db_invoice)
    db.flush()
    for item in invoice_data.items:
        db.add(models.InvoiceItem(invoice_id=db_invoice.id, description=item.description, quantity=item.quantity, unit_price=item.unit_price, total_price=item.quantity * item.unit_price))
    db.commit()
    db.refresh(db_invoice)
    return db_invoice

@app.delete("/invoices/{invoice_id}")
def delete_invoice(invoice_id: int, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    db_invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id, models.Invoice.owner_id == current_user.id).first()
    if not db_invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    db.delete(db_invoice)
    db.commit()
    return {"detail": "Invoice deleted"}
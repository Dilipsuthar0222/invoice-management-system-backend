import os
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List
import models
import schemas
from fastapi.staticfiles import StaticFiles
from database import engine, get_db, Base
from datetime import datetime, timedelta
from jose import JWTError, jwt
import bcrypt
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from auth_utils import router as auth_router
from voice_service import router as voice_router
# Load environment variables
load_dotenv()

# Initialize Database
Base.metadata.create_all(bind=engine)
try:
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN is_2fa_enabled TINYINT(1) DEFAULT 0"))
        conn.execute(text("ALTER TABLE users ADD COLUMN two_factor_secret VARCHAR(255) NULL"))
        conn.commit()
except Exception:
    pass  # Columns already exist

import stripe

# Triggering uvicorn reload to load updated .env
app = FastAPI(title="Dk Developers Invoice API", version="2.0.0")

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_dummy")

app.include_router(auth_router)
app.include_router(voice_router)

# CRITICAL: This serves the PDF files so Twilio can download them
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=CURRENT_DIR), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "API is running. Please use /api/voice/whatsapp for Twilio webhooks."}

@app.post("/")
async def root_webhook(request: Request):
    # This allows the short URL to work automatically!
    from voice_service import whatsapp_webhook
    return await whatsapp_webhook(request)

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
    
    # Check if 2FA is active for this user
    if user.is_2fa_enabled:
        return {"require_2fa": True, "email": user.email}
        
    return {"access_token": create_access_token(data={"sub": user.email}), "token_type": "bearer"}

@app.post("/auth/2fa/login", response_model=schemas.Token)
def login_2fa(request: schemas.TwoFAVerifyRequest, db: Session = Depends(get_db)):
    email = request.email
    code = request.code
    
    # 1. Fetch user from DB
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # 2. Check if 2FA is active
    if not user.is_2fa_enabled or not user.two_factor_secret:
        raise HTTPException(status_code=400, detail="2FA is not enabled for this user")
        
    # 3. Verify TOTP code
    import pyotp
    totp = pyotp.TOTP(user.two_factor_secret)
    if not totp.verify(code):
        raise HTTPException(status_code=400, detail="Invalid 2FA verification code")
        
    # 4. Generate final JWT access token
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
    if client.email:
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

# ============ AGENCY SAFETY ROUTES ============

@app.post("/agency-safety", response_model=schemas.AgencySafetyResponse)
def create_agency_safety_link(data: schemas.AgencySafetyCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    db_item = models.AgencySafetyLink(
        owner_id=current_user.id,
        invoice_id=data.invoice_id,
        image_data=data.image_data,
        watermark_text=data.watermark_text,
        opacity=data.opacity
    )
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    
    return db_item

@app.get("/agency-safety/public/{secure_id}", response_model=schemas.AgencySafetyResponse)
def get_public_agency_safety_link(secure_id: str, db: Session = Depends(get_db)):
    db_item = db.query(models.AgencySafetyLink).filter(models.AgencySafetyLink.secure_id == secure_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Secure link not found")
        
    return db_item

@app.post("/create-payment-intent/{secure_id}")
def create_payment_intent(secure_id: str, db: Session = Depends(get_db)):
    db_item = db.query(models.AgencySafetyLink).filter(models.AgencySafetyLink.secure_id == secure_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Secure link not found")
        
    amount = 5000 # Default $50.00
    if db_item.invoice and db_item.invoice.total_amount > 0:
        amount = int(db_item.invoice.total_amount * 100)
        if amount < 50: # Stripe minimum
            amount = 5000
        
    try:
        # Create a PaymentIntent with the order amount and currency
        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency='usd',
            metadata={'secure_id': secure_id, 'invoice_id': db_item.invoice_id}
        )
        return {"clientSecret": intent.client_secret}
    except Exception as e:
        # If stripe is not configured, return a mock secret for UI testing purposes
        return {"error": str(e), "mock": True}

@app.post("/agency-safety/{secure_id}/clear-payment")
def clear_agency_safety_payment(secure_id: str, db: Session = Depends(get_db)):
    # This is a mock endpoint to simulate invoice payment
    db_item = db.query(models.AgencySafetyLink).filter(models.AgencySafetyLink.secure_id == secure_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Secure link not found")
        
    if db_item.invoice:
        db_item.invoice.status = "Paid"
        db.commit()
        
    return {"detail": "Payment cleared successfully", "status": "Paid"}

# ============ AI INVOICE ENDPOINT ============
import json
import re as _re

# Use Pydantic from schemas to avoid re-import conflicts
from schemas import BaseModel as _SchemaBase
from typing import Optional as _Optional

class AIInvoiceRequest(_SchemaBase):
    requirement: str
    client_name: _Optional[str] = None
    client_email: _Optional[str] = None
    client_phone: _Optional[str] = None
    client_address: _Optional[str] = None

def _smart_parse(requirement: str, client_name: str = "", client_email: str = "",
                 client_phone: str = "", client_address: str = "") -> dict:
    """
    Pure-Python smart parser — works without any AI.
    Detects service category and generates 6-7 professional, context-specific
    invoice line items. Falls back to generic items if category is unknown.
    """
    text = requirement.strip()
    tl   = text.lower()

    # ── 1. Extract amount ────────────────────────────────────────────────────
    total_amount = 0.0
    amount_patterns = [
        r'(?:cost|costs?|cose|price|amount|rs\.?|inr|₹|total)\s*(?:is|:)?\s*([\d,]+)',
        r'([\d,]+)\s*(?:rs\.?|inr|only|rupees?)',
        r'(?:for|of)\s*([\d,]+)(?!\s*(?:item|service|point|feature))',
        r'([\d]{4,})',
    ]
    for pat in amount_patterns:
        m = _re.search(pat, text, _re.I)
        if m:
            clean = _re.sub(r'[^\d]', '', m.group(1))
            if clean and int(clean) > 0:
                total_amount = float(clean)
                break

    # ── 2. Detect if user explicitly listed items ────────────────────────────
    explicit_services = []
    keypoint_match = _re.search(
        r'(?:keypoints?|add|include|services?|features?|items?|deliverables?)\s*[-:]\s*(.+?)'
        r'(?=\band\s+it\b|\band\s+the\b|total|amount|cost|price|rs\.?|inr|₹|\d{4}|$)',
        text, _re.I | _re.S
    )
    if keypoint_match:
        raw_list = keypoint_match.group(1)
        parts = _re.split(r'[,\n•]\s*|\s+and\s+', raw_list, flags=_re.I)
        for p in parts:
            p = p.strip().strip('-').strip()
            p = _re.sub(r'^(?:the|a|an|also|plus|with|add|include)\s+', '', p, flags=_re.I)
            if len(p) > 2 and not _re.match(r'^[\d\s]+$', p):
                explicit_services.append(p.title())

    # ── 3. Category-based default service lists (6-7 items each) ────────────
    if len(explicit_services) >= 4:
        # User gave explicit list — honour it but pad to 6 if needed
        services = explicit_services
    elif any(w in tl for w in ['website', 'web dev', 'web development', 'web app',
                                'webapp', 'react', 'angular', 'vue', 'nextjs',
                                'next.js', 'html', 'css', 'wordpress', 'landing page']):
        services = [
            'UI/UX Design & Wireframing',
            'Frontend Development (HTML/CSS/JS)',
            'Backend API Development',
            'Database Design & Integration',
            'Testing & Quality Assurance',
            'Deployment & Server Setup',
            'Post-Launch Support (30 Days)',
        ]
    elif any(w in tl for w in ['mobile', 'android', 'ios', 'flutter',
                                'react native', 'swift', 'kotlin', 'app dev']):
        services = [
            'App UI/UX Design',
            'Mobile App Development',
            'Backend & API Integration',
            'Database & Storage Setup',
            'Testing & QA (iOS/Android)',
            'App Store Submission',
            'Post-Launch Support (30 Days)',
        ]
    elif any(w in tl for w in ['logo', 'brand', 'branding', 'graphic',
                                'visual identity', 'illustrat']):
        services = [
            'Brand Discovery & Research',
            'Logo Concept Development',
            'Logo Design (Primary + Variants)',
            'Brand Style Guide',
            'Social Media Kit',
            'Final File Delivery (AI/PNG/SVG/PDF)',
        ]
    elif any(w in tl for w in ['seo', 'digital marketing', 'google ads',
                                'ppc', 'social media marketing', 'content marketing']):
        services = [
            'SEO Audit & Competitive Analysis',
            'Keyword Research & Strategy',
            'On-Page SEO Optimization',
            'Content Creation & Copywriting',
            'Social Media Management',
            'Ad Campaign Setup & Management',
            'Monthly Analytics Reporting',
        ]
    elif any(w in tl for w in ['consult', 'consulting', 'advisory',
                                'strategy', 'coach', 'coaching']):
        services = [
            'Discovery & Requirements Workshop',
            'Business & Market Analysis',
            'Technical Architecture Planning',
            'Strategy Documentation',
            'Implementation Roadmap',
            'Follow-Up Consultation Sessions (3)',
        ]
    elif any(w in tl for w in ['ecommerce', 'e-commerce', 'shopify',
                                'woocommerce', 'online store', 'payment gateway']):
        services = [
            'E-Commerce Design & Theme Setup',
            'Product Catalogue Configuration',
            'Payment Gateway Integration',
            'Order & Inventory Management',
            'SEO & Performance Optimization',
            'Security, SSL & Compliance Setup',
            'Training & Documentation',
        ]
    elif any(w in tl for w in ['video', 'edit', 'animation', 'motion', 'reel', 'youtube']):
        services = [
            'Video Concept & Storyboarding',
            'Raw Footage Review & Selection',
            'Professional Video Editing',
            'Motion Graphics & Animations',
            'Color Grading & Sound Mixing',
            'Final Export (HD/4K)',
        ]
    elif any(w in tl for w in ['content', 'blog', 'article', 'copywriting', 'writing']):
        services = [
            'Content Strategy & Planning',
            'Research & Outline Creation',
            'Article / Blog Writing',
            'SEO Optimization of Content',
            'Proofreading & Editing',
            'Content Delivery & Formatting',
        ]
    elif any(w in tl for w in ['data', 'analytics', 'dashboard', 'power bi',
                                'tableau', 'report', 'machine learning', 'ai', 'ml']):
        services = [
            'Data Requirements Analysis',
            'Data Cleaning & Preprocessing',
            'Dashboard / Model Development',
            'Integration & API Setup',
            'Testing & Validation',
            'Documentation & Handover',
        ]
    else:
        # Generic professional services
        services = [
            'Project Discovery & Planning',
            'Core Development / Implementation',
            'Quality Assurance & Testing',
            'Revisions & Refinements',
            'Final Delivery & Documentation',
            'Support & Maintenance (30 Days)',
        ]

    # ── 4. Distribute amount across services ─────────────────────────────────
    n = len(services)
    if total_amount > 0 and n > 0:
        per_item = round(total_amount / n, 2)
        items = [{"description": s, "quantity": 1, "unit_price": per_item} for s in services]
        diff = total_amount - per_item * n
        items[-1]["unit_price"] = round(per_item + diff, 2)
    else:
        items = [{"description": s, "quantity": 1, "unit_price": 0.0} for s in services]

    # ── 5. Client extraction ─────────────────────────────────────────────────
    extracted_name = client_name or ""
    if not extracted_name:
        name_match = _re.search(
            r'(?:client|for|to)\s+(?:the\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})',
            text, _re.I
        )
        if name_match:
            extracted_name = name_match.group(1).title()

    return {
        "client_name":    extracted_name or "Customer",
        "client_email":   client_email or "",
        "client_phone":   client_phone or "",
        "client_address": client_address or "",
        "items": items,
    }

@app.post("/invoices/generate-ai")
def generate_ai_invoice(
    request_data: AIInvoiceRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    requirement = request_data.requirement.strip()
    # Capture user id as a plain int now — stays valid even after db.rollback()
    owner_id = int(current_user.id)

    # ── Step 1: Start with smart Python parser (guaranteed to always work) ───
    extracted_data = _smart_parse(
        requirement=requirement,
        client_name=(request_data.client_name or "").strip(),
        client_email=(request_data.client_email or "").strip(),
        client_phone=(request_data.client_phone or "").strip(),
        client_address=(request_data.client_address or "").strip(),
    )

    # ── Step 2: Try to upgrade with Gemini AI (gemini-1.5-flash) ────────────
    # Only attempt AI if key is available; swallow all errors gracefully
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            from google import genai as _genai
            from google.genai import types as _gtypes

            _client = _genai.Client(api_key=gemini_key)

            prompt = (
                f"You are an expert invoice generator. Parse this invoice requirement and return ONLY valid JSON.\n"
                f"Requirement: \"{requirement}\"\n"
                f"Client name hint: \"{request_data.client_name or 'extract from text'}\"\n"
                f"Client email: \"{request_data.client_email or ''}\"\n"
                f"Client phone: \"{request_data.client_phone or ''}\"\n"
                f"Client address: \"{request_data.client_address or ''}\"\n\n"
                f"CRITICAL RULES:\n"
                f"1. ALWAYS generate EXACTLY 6 to 7 line items — never fewer, never more.\n"
                f"2. Line items MUST be specific to the service type detected in the requirement.\n"
                f"   - Website/Web Dev: UI/UX Design, Frontend Dev, Backend API, Database, Testing, Deployment, 30-Day Support\n"
                f"   - Mobile App: App Design, Development, API Integration, Testing, Store Submission, Support\n"
                f"   - Branding/Logo: Discovery, Logo Concepts, Style Guide, Social Kit, File Delivery, etc.\n"
                f"   - SEO/Marketing: Audit, Keyword Research, On-Page SEO, Content, Ads, Reporting\n"
                f"   - Consulting: Discovery, Analysis, Planning, Documentation, Roadmap, Follow-ups\n"
                f"   - E-Commerce: Design, Product Setup, Payment Gateway, Order Mgmt, SEO, Security, Training\n"
                f"3. Distribute total amount proportionally (heavier tasks get larger share).\n"
                f"4. Extract client info from requirement text if form fields are empty.\n"
                f"5. The SUM of (quantity × unit_price) across all items MUST equal the total amount stated.\n\n"
                f"Return JSON with keys: client_name, client_email, client_phone, "
                f"client_address, items (list of exactly 6-7 objects with: description, quantity, unit_price)."
            )

            ai_response = _client.models.generate_content(
                model="gemini-1.5-flash",
                contents=[prompt],
                config=_gtypes.GenerateContentConfig(response_mime_type="application/json")
            )
            ai_res = json.loads(ai_response.text)

            # Merge AI result — prefer explicit form fields over AI-extracted values
            extracted_data["client_name"] = (
                (request_data.client_name or "").strip() or
                ai_res.get("client_name", "").strip() or
                "Customer"
            )
            extracted_data["client_email"] = (
                (request_data.client_email or "").strip() or
                ai_res.get("client_email", "")
            )
            extracted_data["client_phone"] = (
                (request_data.client_phone or "").strip() or
                ai_res.get("client_phone", "")
            )
            extracted_data["client_address"] = (
                (request_data.client_address or "").strip() or
                ai_res.get("client_address", "")
            )
            if ai_res.get("items") and isinstance(ai_res["items"], list) and len(ai_res["items"]) > 0:
                extracted_data["items"] = ai_res["items"]

            print(f"✨ Gemini AI enhanced invoice for: {extracted_data['client_name']}")

        except Exception as ai_err:
            # Gemini failed (quota / network / parse error) — fallback already ready
            print(f"⚠️ AI upgrade skipped, using smart parser: {type(ai_err).__name__}: {str(ai_err)[:120]}")

    # ── Step 3: Safety net — ensure at least one item always exists ──────────
    if not extracted_data.get("items"):
        extracted_data["items"] = [{"description": "Professional Services", "quantity": 1, "unit_price": 1000.0}]

    # ── Step 4: Persist client ───────────────────────────────────────────────
    client_name = extracted_data["client_name"].strip() or "Customer"
    db_client = db.query(models.Client).filter(
        models.Client.name == client_name,
        models.Client.owner_id == owner_id
    ).first()

    # ── Look up client by name first ─────────────────────────────────────────
    email_val = extracted_data["client_email"].strip() or None

    if not db_client and email_val:
        # Also try to find by email (in case a client with same email already exists)
        db_client = db.query(models.Client).filter(
            models.Client.email == email_val,
            models.Client.owner_id == owner_id
        ).first()

    if not db_client:
        # Safe insert: try with email; if duplicate key, retry without email
        try:
            db_client = models.Client(
                owner_id=owner_id,
                name=client_name,
                email=email_val,
                phone=extracted_data["client_phone"].strip() or None,
                address=extracted_data["client_address"].strip() or None,
            )
            db.add(db_client)
            db.flush()
        except Exception as insert_err:
            # Likely duplicate email — rollback and insert without email
            print(f"⚠️ Client insert conflict ({insert_err.__class__.__name__}), retrying without email...")
            db.rollback()
            # Try finding by email after rollback
            if email_val:
                db_client = db.query(models.Client).filter(
                    models.Client.email == email_val,
                    models.Client.owner_id == owner_id
                ).first()
            if not db_client:
                db_client = models.Client(
                    owner_id=owner_id,
                    name=client_name,
                    email=None,  # drop email to avoid unique constraint
                    phone=extracted_data["client_phone"].strip() or None,
                    address=extracted_data["client_address"].strip() or None,
                )
                db.add(db_client)
                db.flush()
    else:
        # Client exists — update optional fields only if user explicitly provided them
        if request_data.client_phone and request_data.client_phone.strip():
            db_client.phone = request_data.client_phone.strip()
        if request_data.client_address and request_data.client_address.strip():
            db_client.address = request_data.client_address.strip()
        db.flush()

    # ── Step 5: Persist invoice ──────────────────────────────────────────────
    subtotal = sum(
        float(item.get("quantity", 1)) * float(item.get("unit_price", 0.0))
        for item in extracted_data["items"]
    )
    tax_rate = 18.0
    total = subtotal + (subtotal * tax_rate / 100)

    db_invoice = models.Invoice(
        owner_id=owner_id,
        client_id=db_client.id,
        subtotal=subtotal,
        tax_rate=tax_rate,
        total_amount=total,
        status="Unpaid",
    )
    db.add(db_invoice)
    db.flush()

    for item in extracted_data["items"]:
        qty = int(float(item.get("quantity", 1)))
        price = float(item.get("unit_price", 0.0))
        db.add(models.InvoiceItem(
            invoice_id=db_invoice.id,
            description=str(item.get("description", "Service Item")),
            quantity=qty,
            unit_price=price,
            total_price=qty * price,
        ))

    db.commit()
    db.refresh(db_invoice)

    print(f"✅ AI Invoice created: {db_invoice.invoice_number} for {client_name}")

    return {
        "invoice": {
            "id": db_invoice.id,
            "invoice_number": db_invoice.invoice_number,
            "subtotal": float(db_invoice.subtotal),
            "tax_rate": float(db_invoice.tax_rate),
            "total_amount": float(db_invoice.total_amount),
            "status": db_invoice.status,
            "date_created": db_invoice.date_created.isoformat(),
            "items": [
                {
                    "id": it.id,
                    "description": it.description,
                    "quantity": it.quantity,
                    "unit_price": float(it.unit_price),
                    "total_price": float(it.total_price),
                }
                for it in db_invoice.items
            ],
        },
        "client": {
            "id": db_client.id,
            "name": db_client.name,
            "email": db_client.email,
            "phone": db_client.phone,
            "address": db_client.address,
        },
    }
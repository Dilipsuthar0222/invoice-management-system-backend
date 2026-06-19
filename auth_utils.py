import os
import io
import base64
import pyotp
import qrcode
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from database import get_db
import models
import schemas
from jose import jwt, JWTError
from datetime import datetime, timedelta, timezone
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from pydantic import EmailStr, BaseModel
import bcrypt

# Load environment variables
load_dotenv()

# Dummy in-memory DB kept for visualization/reference
dummy_2fa_db = {}

# Re-use your hashing logic from main.py
def get_password_hash(password: str):
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')

# Using Direct SSL (Port 465) with configuration from .env
conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_FROM=os.getenv("MAIL_FROM"),
    MAIL_PORT=int(os.getenv("MAIL_PORT", 465)),
    MAIL_SERVER=os.getenv("MAIL_SERVER"),
    MAIL_STARTTLS=False,
    MAIL_SSL_TLS=True,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True,
    MAIL_FROM_NAME=os.getenv("MAIL_FROM_NAME")
)

RESET_SECRET_KEY = os.getenv("RESET_SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

router = APIRouter(prefix="/auth", tags=["Auth Services"])

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

# --- ENDPOINT: FORGOT PASSWORD ---
@router.post("/forgot-password")
async def forgot_password(request: schemas.ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == request.email).first()
    
    if not user:
        return {"message": "If this email is registered, a reset link has been sent."}

    expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    token = jwt.encode({"sub": user.email, "exp": expire}, RESET_SECRET_KEY, algorithm=ALGORITHM)

    reset_link = f"http://localhost:3000/reset-password?token={token}"

    html_content = f"""
    <div style="font-family: 'Inter', sans-serif; max-width: 500px; margin: auto; padding: 40px; border: 1px solid #f1f5f9; border-radius: 24px; background-color: #ffffff;">
        <div style="text-align: center; margin-bottom: 24px;">
            <div style="display: inline-block; padding: 12px; background-color: #eff6ff; border-radius: 12px;">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#2563eb" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
            </div>
        </div>
        <h2 style="color: #0f172a; font-size: 24px; font-weight: 800; text-align: center; margin-bottom: 8px;">Reset Password</h2>
        <p style="color: #64748b; font-size: 14px; text-align: center; line-height: 1.6; margin-bottom: 32px;">You requested a reset for your account. Use the secure button below to proceed.</p>
        <div style="text-align: center;">
            <a href="{reset_link}" style="display: inline-block; background-color: #2563eb; color: #ffffff; padding: 14px 28px; text-decoration: none; font-weight: 700; font-size: 14px; border-radius: 12px; transition: all 0.2s;">Reset Private Key</a>
        </div>
        <p style="color: #94a3b8; font-size: 11px; text-align: center; margin-top: 32px; font-style: italic;">This link expires in 15 minutes. Ignore if not requested.</p>
    </div>
    """

    message = MessageSchema(
        subject="Restore Your Account - Dk Developers",
        recipients=[request.email],
        body=html_content,
        subtype=MessageType.html
    )

    try:
        fm = FastMail(conf)
        await fm.send_message(message)
        return {"message": "Reset link sent successfully"}
    except Exception as e:
        print(f"CRITICAL SMTP FAILURE: {str(e)}")
        raise HTTPException(status_code=503, detail="Email service unavailable")

# --- ENDPOINT: RESET PASSWORD ---
@router.post("/reset-password")
async def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(request.token, RESET_SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.hashed_password = get_password_hash(request.new_password)
    db.commit()
    return {"detail": "Account security updated successfully"}

# --- ENDPOINT: SEND INVOICE PDF ---
@router.post("/send-invoice-email")
async def send_invoice_email(
    email: str = Form(...), 
    subject: str = Form(...),
    file: UploadFile = File(...)
):
    message = MessageSchema(
        subject=subject,
        recipients=[email],
        body="Please find your invoice attached to this email.",
        subtype=MessageType.plain,
        attachments=[file]
    )

    fm = FastMail(conf)
    await fm.send_message(message)
    return {"message": "Invoice sent successfully to " + email}

# --- ENDPOINT: 2FA SETUP ---
@router.post("/2fa/setup")
async def setup_2fa(request: schemas.TwoFASetupRequest, db: Session = Depends(get_db)):
    email = request.email
    
    # 1. Check if user exists in our actual user DB
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found in system")
        
    # 2. Generate random base32 secret key
    secret_key = pyotp.random_base32()
    
    # 3. Create TOTP provisioning URI
    totp = pyotp.TOTP(secret_key)
    provisioning_uri = totp.provisioning_uri(name=email, issuer_name="DK Developers")
    
    # 4. Generate QR Code Base64 image
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(provisioning_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    qr_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
    
    # 5. PERSIST IN REAL DATABASE
    user.two_factor_secret = secret_key
    user.is_2fa_enabled = False
    db.commit()
    
    # 6. DUMMY DATABASE UPDATE
    dummy_2fa_db[email] = {
        "secret_key": secret_key,
        "is_2fa_enabled": False
    }
    
    print(f"\n--- REAL & DUMMY DB UPDATE FOR 2FA SETUP ---")
    print(f"User: {email}")
    print(f"Secret Key Saved to DB: {secret_key}")
    print(f"Is 2FA Enabled Saved: False")
    print(f"Current Dummy DB State: {dummy_2fa_db}")
    print(f"--------------------------------------------\n")
    
    return {
        "secret": secret_key,
        "qr_code": f"data:image/png;base64,{qr_base64}"
    }

# --- ENDPOINT: 2FA VERIFY ---
@router.post("/2fa/verify")
async def verify_2fa(request: schemas.TwoFAVerifyRequest, db: Session = Depends(get_db)):
    email = request.email
    code = request.code
    
    # 1. Check if user is in our actual user DB
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found in system")
        
    # 2. Get secret key from the database
    secret_key = user.two_factor_secret
    if not secret_key:
        raise HTTPException(
            status_code=400, 
            detail="2FA is not set up for this user. Please call /auth/2fa/setup first."
        )
        
    # 3. Verify TOTP code
    totp = pyotp.TOTP(secret_key)
    is_valid = totp.verify(code)
    
    if not is_valid:
        raise HTTPException(status_code=400, detail="Invalid 2FA verification code")
        
    # 4. PERSIST IN REAL DATABASE
    user.is_2fa_enabled = True
    db.commit()
    
    # 5. DUMMY DATABASE UPDATE
    dummy_2fa_db[email] = {
        "secret_key": secret_key,
        "is_2fa_enabled": True
    }
    
    print(f"\n--- REAL & DUMMY DB UPDATE FOR 2FA VERIFY ---")
    print(f"User: {email}")
    print(f"Secret Key Verified: {secret_key}")
    print(f"Is 2FA Enabled Updated to: True")
    print(f"Current Dummy DB State: {dummy_2fa_db}")
    print(f"---------------------------------------------\n")
    
    return {
        "success": True,
        "message": "Two-Factor Authentication verified and enabled successfully!"
    }

# --- ENDPOINT: 2FA DISABLE ---
@router.post("/2fa/disable")
async def disable_2fa(request: schemas.TwoFASetupRequest, db: Session = Depends(get_db)):
    email = request.email
    
    # 1. Check if user exists in our actual user DB
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found in system")
        
    # 2. Persist in database
    user.is_2fa_enabled = False
    user.two_factor_secret = None
    db.commit()
    
    # 3. Dummy DB sync
    if email in dummy_2fa_db:
        dummy_2fa_db[email]["is_2fa_enabled"] = False
        dummy_2fa_db[email]["secret_key"] = None
        
    print(f"\n--- REAL & DUMMY DB UPDATE FOR 2FA DISABLE ---")
    print(f"User: {email}")
    print(f"Is 2FA Enabled Set to: False")
    print(f"----------------------------------------------\n")
        
    return {
        "success": True,
        "message": "Two-Factor Authentication disabled successfully."
    }
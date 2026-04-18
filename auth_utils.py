import os
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
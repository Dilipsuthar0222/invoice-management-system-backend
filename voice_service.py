import os
import json
import requests
from fastapi import APIRouter, Request
from fastapi.responses import Response
from google import genai
from google.genai import types
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv
from pdf_generator import InvoicePDF
from database import SessionLocal
import models

load_dotenv()
router = APIRouter()

PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Clients
client = None
if os.getenv("GEMINI_API_KEY"):
    try: client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    except Exception as e: print(f"⚠️ Gemini Client Error: {e}")

twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))

@router.post("/api/voice/whatsapp")
@router.post("/api/voice/whatsapp-voice")
async def whatsapp_webhook(request: Request):
    load_dotenv(override=True)
    try:
        form_data = await request.form()
    except:
        form_data = {}
        
    # CRITICAL FIX: Ensure phone number format is perfect
    sender = form_data.get("From", "").strip().replace(" ", "+")
    if sender and "whatsapp:" in sender and "+" not in sender:
        sender = sender.replace("whatsapp:", "whatsapp:+")
        
    text_body = form_data.get("Body", "")
    media_url = form_data.get("MediaUrl0")
    
    print(f"\n📲 Incoming WhatsApp from: {sender}")
    
    if not sender:
        print("❌ No sender number found in request.")
        return Response(content=str(MessagingResponse()), media_type="application/xml")

    try:
        # 1. AI Extraction (with Professional Parser Fallback)
        invoice_data = {
            "client_name": "Customer",
            "amount": 0,
            "points": ["Services as per request"]
        }
        
        import re
        try:
            text = text_body.strip()
            
            # 1. Full Name Extraction (Capture up to 2 words after 'for' or 'client')
            name_match = re.search(r'(?:for|client)\s+(?:the\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', text, re.I)
            if name_match: 
                invoice_data["client_name"] = name_match.group(1).title()
            
            # 2. Amount Extraction (Handle commas like 1,00,000)
            # Looks for any number with digits and optional commas
            amount_match = re.search(r'(?:of|for|amount|Rs\.?|inr)\s*([\d,]+)|([\d,]+)\s*(?:rs|inr|only)', text, re.I)
            if not amount_match: # Try just finding the last number in the string
                amount_match = re.search(r'(\d[\d,]*\d|\d)$', text)
            
            if amount_match:
                amt_str = amount_match.group(1) or amount_match.group(2) or amount_match.group(0)
                # Remove commas and convert to integer
                clean_amt = re.sub(r'[^\d]', '', amt_str)
                if clean_amt: invoice_data["amount"] = int(clean_amt)
            
            # 3. Advanced Service & Point Extraction
            if name_match:
                start = name_match.end()
                amt_start = text.find(amount_match.group(0)) if amount_match else len(text)
                if amt_start > start:
                    full_service = text[start:amt_start].strip()
                    # Clean up common joiners at start
                    full_service = re.sub(r'^(the|is|for|of|with|a|to)\s+', '', full_service, flags=re.I)
                    
                    # Split into multiple points based on keywords: "with", "and", "add", "including"
                    points = re.split(r'\s+(?:with|and|add|including|plus)\s+', full_service, flags=re.I)
                    
                    # Clean each point
                    clean_points = []
                    for p in points:
                        p = p.strip().title()
                        if len(p) > 2: clean_points.append(p)
                    
                    if clean_points:
                        # Ensure we have at least 4 professional points by adding defaults if needed
                        while len(clean_points) < 4:
                            defaults = ["Custom Development", "Quality Assurance", "Deployment Support", "Maintenance"]
                            for d in defaults:
                                if d not in clean_points:
                                    clean_points.append(d)
                                    break
                            if len(clean_points) >= 4: break
                                    
                        invoice_data["points"] = clean_points[:6] # Max 6 points
        except Exception as e:
            print(f"DEBUG: Advanced parse error: {e}")

        if client and (text_body or media_url):
            try:
                prompt = f"Extract invoice details from: '{text_body}'. Return ONLY JSON: {{'client_name': '...', 'amount': 123, 'points': ['...', '...', '...', '...']}}"
                content = [prompt]
                if media_url:
                    audio_res = requests.get(media_url, auth=(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")))
                    if audio_res.status_code == 200:
                        content = [types.Part.from_bytes(data=audio_res.content, mime_type='audio/ogg'), prompt]

                response = client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=content,
                    config=types.GenerateContentConfig(response_mime_type='application/json')
                )
                ai_data = json.loads(response.text)
                invoice_data.update(ai_data) # Use AI data if successful
                print(f"✨ AI data extracted for: {invoice_data.get('client_name')}")
            except Exception as e:
                print(f"⚠️ AI busy/error, using message details: {invoice_data.get('client_name')} - {invoice_data.get('amount')}")

        # 2. Generate PDF
        # Use the actual client name from the message
        client_name = invoice_data.get('client_name', 'Client')
        filename = f"invoice_{client_name}.pdf".replace(" ", "_")
        pdf_path = os.path.join(BASE_DIR, filename)
        
        pdf = InvoicePDF()
        pdf.generate(invoice_data, pdf_path)
        print(f"📄 PDF generated at: {pdf_path}")

        # 3. Construct URL
        dynamic_public_url = os.getenv("PUBLIC_URL", "").strip()
        base_url = dynamic_public_url.rstrip('/') if dynamic_public_url else f"{request.url.scheme}://{request.headers.get('host')}".rstrip('/')
        pdf_url = f"{base_url}/static/{filename}"
        
        print(f"📄 PDF generated at: {pdf_path}")
        print(f"🔗 Sending PDF Link: {pdf_url}")

        # 4. Save to Database
        db = SessionLocal()
        try:
            # 4.1 Get default owner (first user)
            owner = db.query(models.User).first()
            if owner:
                # 4.2 Find or create client
                client_obj = db.query(models.Client).filter(
                    models.Client.name == invoice_data["client_name"],
                    models.Client.owner_id == owner.id
                ).first()
                
                if not client_obj:
                    client_obj = models.Client(
                        name=invoice_data["client_name"],
                        owner_id=owner.id,
                        phone=sender.replace("whatsapp:", "")
                    )
                    db.add(client_obj)
                    db.flush() # Get client_obj.id
                
                # 4.3 Create Invoice
                new_invoice = models.Invoice(
                    owner_id=owner.id,
                    client_id=client_obj.id,
                    total_amount=invoice_data["amount"],
                    subtotal=invoice_data["amount"],
                    status="Unpaid"
                )
                db.add(new_invoice)
                db.flush() # Get new_invoice.id
                
                # 4.4 Add Invoice Items
                for i, point in enumerate(invoice_data.get("points", [])):
                    # Put the full amount on the first item, others are 0 (or split if you prefer)
                    item_price = invoice_data["amount"] if i == 0 else 0
                    db_item = models.InvoiceItem(
                        invoice_id=new_invoice.id,
                        description=point,
                        quantity=1,
                        unit_price=item_price,
                        total_price=item_price
                    )
                    db.add(db_item)
                
                db.commit()
                print(f"💾 Invoice saved to DB for {invoice_data['client_name']}")
        except Exception as db_err:
            print(f"⚠️ DB Saving failed: {db_err}")
            db.rollback()
        finally:
            db.close()

        # 5. Respond via TwiML
        twiml = MessagingResponse()
        msg = twiml.message()
        msg.body(f"✅ Your invoice for *{invoice_data.get('client_name', 'Dilip')}* is ready!")
        msg.media(pdf_url)
        
        print(f"✅ TwiML Response generated for {sender}")
        return Response(content=str(twiml), media_type="application/xml")

    except Exception as e:
        print(f"❌ Final Webhook Error: {e}")
        twiml = MessagingResponse()
        twiml.message("⚠️ Sorry, I had an error. Please try again.")
        return Response(content=str(twiml), media_type="application/xml")

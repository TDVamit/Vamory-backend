from app.config import settings
import aiohttp
import os
from app.database import get_email_tracking_collection, ensure_email_tracking_collection
from app.models.email_tracking import EmailTrackingCreate, EmailType
from datetime import datetime


async def initialize_email_tracking():
    """Initialize email tracking system - call this when app starts"""
    try:
        success = await ensure_email_tracking_collection()
        if success:
            print("✓ Email tracking system initialized successfully")
        else:
            print("✗ Failed to initialize email tracking system")
        return success
    except Exception as e:
        print(f"Error initializing email tracking: {e}")
        return False


API_KEY = settings.MAILGUN_API_KEY
BASE_URL = settings.MAILGUN_BASE_URL
FROM_NAME = settings.FROM_NAME
EMAIL_DOMAIN = settings.EMAIL_DOMAIN
EMAIL_NAME = settings.EMAIL_NAME
SEND_DOMAIN = settings.SEND_DOMAIN


async def add_domain(domain_name: str) -> dict:
    url = f"{BASE_URL}/domains"
    form = aiohttp.FormData()
    form.add_field("name", domain_name)
    form.add_field("spam_action", "disabled")
    form.add_field("wildcard", "true")
    form.add_field("use_automatic_sender_security", "true")

    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            data=form,
            auth=aiohttp.BasicAuth("api", API_KEY)
        ) as response:
            return await response.json()
        
async def verify_domain(domain_name: str) -> dict:
    url = f"{BASE_URL}/domains/{domain_name}/verify"
    
    async with aiohttp.ClientSession() as session:
        async with session.put(
            url,
            auth=aiohttp.BasicAuth("api", API_KEY)
        ) as response:
            return await response.json()
    
async def send(
    to: str,
    subject: str,
    content: str,
    domain_name: str = EMAIL_DOMAIN,
    send_domain: str = SEND_DOMAIN,
    email_name: str = EMAIL_NAME,
    name : str = FROM_NAME,
    attachment: str = None
) -> dict:
    try:
        url = f"{BASE_URL}/{domain_name}/messages"
        form = aiohttp.FormData()
        form.add_field("from", f"{name} <{email_name}@{send_domain}>")
        form.add_field("to", to)
        form.add_field("subject", subject)
        form.add_field("html", content)
        if attachment and os.path.exists(attachment):
            print(True)
            async with aiohttp.ClientSession() as session:
                with open(attachment, 'rb') as f:
                    form.add_field(
                        "attachment",
                        f,
                        filename=os.path.basename(attachment),
                        content_type="application/pdf"
                    )
                    async with session.post(url, data=form, auth=aiohttp.BasicAuth("api", API_KEY)) as response:
                        return await response.json()
        else:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=form, auth=aiohttp.BasicAuth("api", API_KEY)) as response:
                    return await response.json()
    except Exception as e:
        print(f"Error sending email: {e}")
        return {"status": "error", "message": f"Failed to send email.", "details": str(e)}
    
        
async def delete_domain(domain_name: str) -> dict:
    url = f"{BASE_URL}/domains/{domain_name}"
    
    async with aiohttp.ClientSession() as session:
        async with session.delete(url, auth=aiohttp.BasicAuth("api", API_KEY)) as response:
            data = await response.json()
            if response.status == 200:
                return {"status": "success", "message": f"Domain {domain_name} deleted successfully."}
            else:
                return {"status": "error", "message": f"Failed to delete domain {domain_name}.", "details": data}


async def track_email_send(email_type: EmailType, to_email: str, user_name: str, subject: str, credit_amount: str = None) -> str:
    """Track email send in database and return the tracking ID"""
    try:
        collection = await get_email_tracking_collection()
        if collection is None:
            print("Warning: Database collection is None, skipping email tracking")
            return None
            
        email_tracking = EmailTrackingCreate(
            email_type=email_type,
            to_email=to_email,
            user_name=user_name,
            credit_amount=credit_amount,
            subject=subject
        )
        result = await collection.insert_one(email_tracking.dict())
        return str(result.inserted_id)
    except Exception as e:
        print(f"Error tracking email: {e}")
        return None


async def check_recent_email_sent(email_type: EmailType, to_email: str, user_name: str) -> bool:
    """Check if a similar email was sent recently (within 24 hours for credit emails)"""
    try:
        collection = await get_email_tracking_collection()
        if collection is None:
            print("Warning: Database collection is None, skipping recent email check")
            return False
        
        # For credit-related emails, check if same type was sent to same user recently
        if email_type in [EmailType.LOW_CREDIT, EmailType.CREDITS_EXPIRED]:
            # Check for emails sent in the last 24 hours
            from datetime import timedelta
            cutoff_time = datetime.utcnow() - timedelta(hours=24)
            
            query = {
                "email_type": email_type,
                "to_email": to_email,
                "user_name": user_name,
                "sent_at": {"$gte": cutoff_time}
            }
            
            
            existing_email = await collection.find_one(query)
            return existing_email is not None
        
        return False
    except Exception as e:
        print(f"Error checking recent email: {e}")
        return False


async def delete_credit_emails_for_user(to_email: str, user_name: str):
    """Delete all credit-related email tracking entries for a user after successful payment"""
    try:
        collection = await get_email_tracking_collection()
        if collection is None:
            print("Warning: Database collection is None, skipping credit email deletion")
            return 0
            
        result = await collection.delete_many({
            "to_email": to_email,
            "user_name": user_name,
            "email_type": {"$in": [EmailType.LOW_CREDIT, EmailType.CREDITS_EXPIRED]}
        })
        print(f"Deleted {result.deleted_count} credit email tracking entries for {user_name}")
        return result.deleted_count
    except Exception as e:
        print(f"Error deleting credit email tracking: {e}")
        return 0

async def send_retrieval_success_message(foldername,toemail,retrieval_days,retrieval_expiry_date):
    subject = f"🎉 Retrieval Successful: {foldername} is Ready!"
    content = """
<!-- Email-friendly retrieval success HTML (desktop-friendly).
     Note: many email clients prefer widths 600px; for better Windows/desktop visibility this version uses 700px container,
     larger logo, bigger fonts and paddings. Still uses table + inline styles for broad compatibility.
     Replace {{folder_name}}, {{retrieval_days}}, {{expiry_date}} with your template values.
-->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Retrieval Successful - Vamory</title>
</head>
<body style="margin:0;padding:0;background-color:#f7fafc;font-family:Arial,Helvetica,sans-serif;">
  <!-- wrapper table -->
  <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background-color:#f7fafc;">
    <tr>
      <td align="center">
        <!-- container table (wider for desktop/windows clients) -->
        <table width="700" cellpadding="0" cellspacing="0" role="presentation" style="max-width:700px;margin:0 auto;">
          <!-- header -->
          <tr>
            <td align="center" bgcolor="#101214" style="padding:20px 24px;text-align:center;">
              <a href="https://vamory.vadaevri.com" target="_blank" style="text-decoration:none;display:inline-block;">
                <img src="https://vamory-s3-bucket-by-vamit.s3.amazonaws.com/logo/VD%20Logo%20Funky.png" alt="Vamory" width="180" style="display:block;border:0;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic;" />
              </a>
            </td>
          </tr>

          <!-- body card -->
          <tr>
            <td align="center" style="padding:44px 20px;">
              <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#ffffff;border-radius:12px;border:1px solid #e9eef2;">
                <tr>
                  <td style="padding:36px 40px 44px 40px;text-align:center;">
                    <div style="width:80px;height:80px;margin:0 auto 18px;border-radius:999px;display:inline-block;background:#10b981;color:#ffffff;font-weight:700;line-height:80px;font-size:36px;">✓</div>
                    <h1 style="font-size:22px;margin:0 0 10px;color:#111827;">Retrieval Successful</h1>
                    <p style="color:#374151;margin:0 0 20px;line-height:1.5;font-size:16px;">Your requested retrieval of folder <strong>{{folder_name}}</strong> for <strong>{{retrieval_days}}</strong> days — valid till <strong>{{expiry_date}}</strong>.</p>

                    <!-- info boxes (side-by-side on wide clients) -->
                    <table cellpadding="0" cellspacing="0" role="presentation" style="margin:20px auto 0;">
                      <tr>
                        <td style="padding:12px 18px; background:#f8fafc;border-radius:8px;border:1px solid #eef3f7;min-width:200px;text-align:center;font-size:15px;font-weight:600;color:#111827;">Folder<br><span style="font-weight:700;display:block;margin-top:8px;font-size:16px;">{{folder_name}}</span></td>
                        <td width="16">&nbsp;</td>
                        <td style="padding:12px 18px; background:#f8fafc;border-radius:8px;border:1px solid #eef3f7;min-width:200px;text-align:center;font-size:15px;font-weight:600;color:#111827;">Expires On<br><span style="font-weight:700;display:block;margin-top:8px;font-size:16px;">{{expiry_date}}</span></td>
                      </tr>
                    </table>

                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- footer -->
          <tr>
            <td align="center" bgcolor="#101214" style="padding:18px 24px;text-align:center;color:#d1d5db;font-size:14px;">
              <div style="margin-bottom:8px;">
                <a href="mailto:support@vamory.vadaevri.com" style="color:#d1d5db;text-decoration:none;margin-right:14px;">✉ support@vamory.vadaevri.com</a>
                <a href="https://vamory.vadaevri.com" style="color:#d1d5db;text-decoration:none;">🌐 vamory.vadaevri.com</a>
              </div>
              <div style="color:#9ca3af;font-size:13px;">© 2025 Vamory</div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>

    """

    content = content.replace("{{folder_name}}", foldername)
    content = content.replace("{{retrieval_days}}", retrieval_days)
    content = content.replace("{{expiry_date}}", retrieval_expiry_date)
    
    # Track email send (non-blocking)
    try:
        await track_email_send(
            email_type=EmailType.RETRIEVAL_SUCCESS,
            to_email=toemail,
            user_name="",  # No specific user name for retrieval emails
            subject=subject
        )
    except Exception as e:
        print(f"Warning: Failed to track retrieval email: {e}")
    
    return await send(to=toemail, subject=subject, content=content)

async def send_folder_shared_message(foldername,toemail,shared_from):
    subject = f"{shared_from} shared a folder with you: {foldername} — Vamory"
    content = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Folder Shared - Vamory</title>
</head>
<body style="margin:0;padding:0;background-color:#f7fafc;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background-color:#f7fafc;">
    <tr>
      <td align="center">
        <table width="700" cellpadding="0" cellspacing="0" role="presentation" style="max-width:700px;margin:0 auto;">
          <tr>
            <td align="center" bgcolor="#101214" style="padding:20px 24px;text-align:center;">
              <a href="https://vamory.vadaevri.com" target="_blank" style="text-decoration:none;display:inline-block;">
                <img src="https://vamory-s3-bucket-by-vamit.s3.amazonaws.com/logo/VD%20Logo%20Funky.png" alt="Vamory" width="180" style="display:block;border:0;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic;" />
              </a>
            </td>
          </tr>

          <tr>
            <td align="center" style="padding:44px 20px;">
              <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#ffffff;border-radius:12px;border:1px solid #e9eef2;">
                <tr>
                  <td style="padding:36px 40px 44px 40px;text-align:center;">
                    <div style="width:80px;height:80px;margin:0 auto 18px;border-radius:999px;display:inline-block;background:#3b82f6;color:#ffffff;font-weight:700;line-height:80px;font-size:36px;">⇪</div>
                    <h1 style="font-size:22px;margin:0 0 10px;color:#111827;">Folder Shared</h1>
                    <p style="color:#374151;margin:0 0 20px;line-height:1.5;font-size:16px;"><strong>{{from_name}}</strong> shared a folder <strong>{{folder_name}}</strong> with you.</p>

                    <table cellpadding="0" cellspacing="0" role="presentation" style="margin:20px auto 0;">
                      <tr>
                        <td style="padding:12px 18px; background:#f8fafc;border-radius:8px;border:1px solid #eef3f7;min-width:200px;text-align:center;font-size:15px;font-weight:600;color:#111827;">Folder<br><span style="font-weight:700;display:block;margin-top:8px;font-size:16px;">{{folder_name}}</span></td>
                        <td width="16">&nbsp;</td>
                        <td style="padding:12px 18px; background:#f8fafc;border-radius:8px;border:1px solid #eef3f7;min-width:200px;text-align:center;font-size:15px;font-weight:600;color:#111827;">Shared By<br><span style="font-weight:700;display:block;margin-top:8px;font-size:16px;">{{from_name}}</span></td>
                      </tr>
                    </table>

                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td align="center" bgcolor="#101214" style="padding:18px 24px;text-align:center;color:#d1d5db;font-size:14px;">
              <div style="margin-bottom:8px;">
                <a href="mailto:support@vamory.vadaevri.com" style="color:#d1d5db;text-decoration:none;margin-right:14px;">✉ support@vamory.vadaevri.com</a>
                <a href="https://vamory.vadaevri.com" style="color:#d1d5db;text-decoration:none;">🌐 vamory.vadaevri.com</a>
              </div>
              <div style="color:#9ca3af;font-size:13px;">© 2025 Vamory</div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>

    """
    content = content.replace("{{folder_name}}", foldername)
    content = content.replace("{{from_name}}", shared_from)
    
    # Track email send (non-blocking)
    try:
        await track_email_send(
            email_type=EmailType.FOLDER_SHARED,
            to_email=toemail,
            user_name=shared_from,
            subject=subject
        )
    except Exception as e:
        print(f"Warning: Failed to track folder shared email: {e}")
    
    return await send(to=toemail, subject=subject, content=content)

async def send_payment_success_message(amount,toemail,name,invoice_attachment):
    subject = f"🎉 Payment Successful - Credit Added to Your Vamory Account"
    content = """
    <!-- Email-friendly "payment successful" notification (desktop-friendly).
     Replace {{name}}, {{credit_amount}} with your template values. Attach invoice as a file when sending the email.
-->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Payment Successful - Vamory</title>
</head>
<body style="margin:0;padding:0;background-color:#f7fafc;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background-color:#f7fafc;">
    <tr>
      <td align="center">
        <table width="700" cellpadding="0" cellspacing="0" role="presentation" style="max-width:700px;margin:0 auto;">
          <tr>
            <td align="center" bgcolor="#101214" style="padding:20px 24px;text-align:center;">
              <a href="https://vamory.vadaevri.com" target="_blank" style="text-decoration:none;display:inline-block;">
                <img src="https://vamory-s3-bucket-by-vamit.s3.amazonaws.com/logo/VD%20Logo%20Funky.png" alt="Vamory" width="180" style="display:block;border:0;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic;" />
              </a>
            </td>
          </tr>

          <tr>
            <td align="center" style="padding:44px 20px;">
              <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#ffffff;border-radius:12px;border:1px solid #e9eef2;">
                <tr>
                  <td style="padding:36px 40px 44px 40px;text-align:center;">
                    <div style="width:80px;height:80px;margin:0 auto 18px;border-radius:999px;display:inline-block;background:#10b981;color:#ffffff;font-weight:700;line-height:80px;font-size:36px;">✔</div>
                    <h1 style="font-size:22px;margin:0 0 10px;color:#111827;">Payment Successful</h1>
                    <p style="color:#374151;margin:0 0 20px;line-height:1.5;font-size:16px;">Thank you <strong>{{name}}</strong>, your payment was successful. <strong>Added credit: {{credit_amount}}</strong>.</p>
                    <p style="color:#374151;margin:0 0 18px;line-height:1.4;font-size:14px;">Please find the invoice attached to this email for your records.</p>

                    <table cellpadding="0" cellspacing="0" role="presentation" style="margin:20px auto 0;">
                      <tr>
                        <td style="padding:12px 18px; background:#f8fafc;border-radius:8px;border:1px solid #eef3f7;min-width:220px;text-align:center;font-size:15px;font-weight:600;color:#111827;">Amount Credited<br><span style="font-weight:700;display:block;margin-top:8px;font-size:16px;">{{credit_amount}}</span></td>
                        <td width="16">&nbsp;</td>
                        <td style="padding:12px 18px; background:#f8fafc;border-radius:8px;border:1px solid #eef3f7;min-width:220px;text-align:center;font-size:15px;font-weight:600;color:#111827;">Account Name<br><span style="font-weight:700;display:block;margin-top:8px;font-size:16px;">{{name}}</span></td>
                      </tr>
                    </table>

                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td align="center" bgcolor="#101214" style="padding:18px 24px;text-align:center;color:#d1d5db;font-size:14px;">
              <div style="margin-bottom:8px;">
                <a href="mailto:support@vamory.vadaevri.com" style="color:#d1d5db;text-decoration:none;margin-right:14px;">✉ support@vamory.vadaevri.com</a>
                <a href="https://vamory.vadaevri.com" style="color:#d1d5db;text-decoration:none;">🌐 vamory.vadaevri.com</a>
              </div>
              <div style="color:#9ca3af;font-size:13px;">© 2025 Vamory</div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>

    """
    content = content.replace("{{credit_amount}}", amount)
    content = content.replace("{{name}}", name)
    
    # Track email send (non-blocking)
    try:
        await track_email_send(
            email_type=EmailType.PAYMENT_SUCCESS,
            to_email=toemail,
            user_name=name,
            subject=subject,
            credit_amount=amount
        )
        
        # Delete all credit-related email tracking entries for this user
        await delete_credit_emails_for_user(toemail, name)
    except Exception as e:
        print(f"Warning: Failed to track payment success email: {e}")
    
    return await send(to=toemail, subject=subject, content=content,attachment=invoice_attachment)

async def send_low_credit_message(toemail,name,credit_amount):
    # Check if we've already sent a low credit email recently
    if await check_recent_email_sent(EmailType.LOW_CREDIT, toemail, name):
        print(f"Low credit email already sent recently to {toemail} for {name} with amount {credit_amount}")
        return {"status": "skipped", "message": "Low credit email already sent recently"}
    
    subject = f"⚠️ Low Credit Warning - Action Required"
    content = """
    <!doctype html>
    <html lang="en">
    <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>Low Credit Warning - Vamory</title>
    </head>
    <body style="margin:0;padding:0;background-color:#ffffff;font-family:Arial,Helvetica,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background-color:#ffffff;">
        <tr>
        <td align="center">
            <table width="700" cellpadding="0" cellspacing="0" role="presentation" style="max-width:700px;margin:0 auto;">
            <tr>
                <td align="center" bgcolor="#101214" style="padding:20px 24px;text-align:center;">
                <a href="https://vamory.vadaevri.com" target="_blank" style="text-decoration:none;display:inline-block;">
                    <img src="https://vamory-s3-bucket-by-vamit.s3.amazonaws.com/logo/VD%20Logo%20Funky.png" alt="Vamory" width="180" style="display:block;border:0;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic;" />
                </a>
                </td>
            </tr>

            <tr>
                <td align="center" style="padding:44px 20px;">
                <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#ffffff;border-radius:12px;border:1px solid #fde3cf;">
                    <tr>
                    <td style="padding:36px 40px 44px 40px;text-align:center;">
                        <div style="width:80px;height:80px;margin:0 auto 18px;border-radius:999px;display:inline-block;background:#f97316;color:#ffffff;font-weight:700;line-height:80px;font-size:36px;">!</div>
                        <h1 style="font-size:22px;margin:0 0 10px;color:#111827;">Low Credit Warning</h1>
                        <p style="color:#374151;margin:0 0 18px;line-height:1.5;font-size:16px;">Hello <strong>{{name}}</strong>, your current credit balance is <strong>{{current_credit}}</strong>. Please add credit to avoid service interruption.</p>

                        <p style="color:#b45309;margin:0 0 18px;line-height:1.4;font-size:15px;font-weight:700;">Warning: Your files will be deleted if you don't add credit on time.</p>

                        <table cellpadding="0" cellspacing="0" role="presentation" style="margin:20px auto 0;">
                        <tr>
                            <td style="padding:12px 18px; background:#ffffff;border-radius:8px;border:1px solid #fde3cf;min-width:220px;text-align:center;font-size:15px;font-weight:600;color:#111827;">Current Credit<br><span style="font-weight:700;display:block;margin-top:8px;font-size:16px;">{{current_credit}}</span></td>
                            <td width="16">&nbsp;</td>
                            <td style="padding:12px 18px; background:#ffffff;border-radius:8px;border:1px solid #fde3cf;min-width:220px;text-align:center;font-size:15px;font-weight:600;color:#111827;">Account<br><span style="font-weight:700;display:block;margin-top:8px;font-size:16px;">{{name}}</span></td>
                        </tr>
                        </table>

                        <div style="margin-top:22px;">
                        <!-- CTA button: replace href with real top-up link -->
                        <a href="https://vamory.vadaevri.com/upgrade" style="background:#f97316;color:#ffffff;padding:12px 20px;border-radius:8px;text-decoration:none;display:inline-block;font-weight:700;">Add Credit</a>
                        </div>

                    </td>
                    </tr>
                </table>
                </td>
            </tr>

            <tr>
                <td align="center" bgcolor="#101214" style="padding:18px 24px;text-align:center;color:#d1d5db;font-size:14px;">
                <div style="margin-bottom:8px;">
                    <a href="mailto:support@vamory.vadaevri.com" style="color:#d1d5db;text-decoration:none;margin-right:14px;">✉ support@vamory.vadaevri.com</a>
                    <a href="https://vamory.vadaevri.com" style="color:#d1d5db;text-decoration:none;">🌐 vamory.vadaevri.com</a>
                </div>
                <div style="color:#9ca3af;font-size:13px;">© 2025 Vamory</div>
                </td>
            </tr>

            </table>
        </td>
        </tr>
    </table>
    </body>
    </html>
    """
    content = content.replace("{{current_credit}}", credit_amount)
    content = content.replace("{{name}}", name)
    
    # Track email send (non-blocking)
    try:
        await track_email_send(
            email_type=EmailType.LOW_CREDIT,
            to_email=toemail,
            user_name=name,
            subject=subject,
            credit_amount=credit_amount
        )
    except Exception as e:
        print(f"Warning: Failed to track low credit email: {e}")
    
    return await send(to=toemail, subject=subject, content=content)

async def send_credits_expired_message(toemail,name):
    # Check if we've already sent a credits expired email recently
    if await check_recent_email_sent(EmailType.CREDITS_EXPIRED, toemail, name):
        print(f"Credits expired email already sent recently to {toemail} for {name}")
        return {"status": "skipped", "message": "Credits expired email already sent recently"}
    
    subject = f"Credits Expired"
    content = """
    <!-- Email-friendly "zero credit" notification (desktop-friendly).
     Replace {{name}} with your template value. This template tells the user they have 0 credit, files will be deleted in 60 days,
     and that files can be downloaded after adding credit. Keep the Add Credit CTA updated to your top-up link.
-->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>No Credit - Vamory</title>
</head>
<body style="margin:0;padding:0;background-color:#ffffff;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background-color:#ffffff;">
    <tr>
      <td align="center">
        <table width="700" cellpadding="0" cellspacing="0" role="presentation" style="max-width:700px;margin:0 auto;">
          <tr>
            <td align="center" bgcolor="#101214" style="padding:20px 24px;text-align:center;">
              <a href="https://vamory.vadaevri.com" target="_blank" style="text-decoration:none;display:inline-block;">
                <img src="https://vamory-s3-bucket-by-vamit.s3.amazonaws.com/logo/VD%20Logo%20Funky.png" alt="Vamory" width="180" style="display:block;border:0;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic;" />
              </a>
            </td>
          </tr>

          <tr>
            <td align="center" style="padding:44px 20px;">
              <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#ffffff;border-radius:12px;border:1px solid #fde3cf;">
                <tr>
                  <td style="padding:36px 40px 44px 40px;text-align:center;">
                    <div style="width:80px;height:80px;margin:0 auto 18px;border-radius:999px;display:inline-block;background:#ef4444;color:#ffffff;font-weight:700;line-height:80px;font-size:36px;">!</div>
                    <h1 style="font-size:22px;margin:0 0 10px;color:#111827;">Account Suspended — No Credit</h1>
                    <p style="color:#374151;margin:0 0 18px;line-height:1.5;font-size:16px;">Hello <strong>{{name}}</strong>, you currently have <strong>0 credit</strong> left. Please add credit to resume your service.</p>

                    <p style="color:#b91c1c;margin:0 0 18px;line-height:1.4;font-size:15px;font-weight:700;">Important: Your files will be deleted in <strong>60 days</strong> if you do not add credit.</p>

                    <p style="color:#374151;margin:0 0 20px;line-height:1.5;font-size:14px;">After you add credit, you will be able to download your files. Until then, downloads are disabled.</p>

                    <table cellpadding="0" cellspacing="0" role="presentation" style="margin:20px auto 0;">
                      <tr>
                        <td style="padding:12px 18px; background:#ffffff;border-radius:8px;border:1px solid #fde3cf;min-width:220px;text-align:center;font-size:15px;font-weight:600;color:#111827;">Status<br><span style="font-weight:700;display:block;margin-top:8px;font-size:16px;">0 Credit</span></td>
                        <td width="16">&nbsp;</td>
                        <td style="padding:12px 18px; background:#ffffff;border-radius:8px;border:1px solid #fde3cf;min-width:220px;text-align:center;font-size:15px;font-weight:600;color:#111827;">Deletion Window<br><span style="font-weight:700;display:block;margin-top:8px;font-size:16px;">60 days</span></td>
                      </tr>
                    </table>

                    <div style="margin-top:22px;display:flex;gap:12px;justify-content:center;flex-wrap:wrap;">
                      <!-- CTA buttons: update hrefs as needed -->
                      <a href="https://vamory.vadaevri.com/upgrade" style="background:#ef4444;color:#ffffff;padding:12px 20px;border-radius:8px;text-decoration:none;display:inline-block;font-weight:700;">Add Credit</a>
                      <a href="https://vamory.vadaevri.com/login" style="background:#ffffff;color:#111827;padding:12px 20px;border-radius:8px;border:1px solid #e5e7eb;text-decoration:none;display:inline-block;font-weight:700;">Manage Account</a>
                    </div>

                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td align="center" bgcolor="#101214" style="padding:18px 24px;text-align:center;color:#d1d5db;font-size:14px;">
              <div style="margin-bottom:8px;">
                <a href="mailto:support@vamory.vadaevri.com" style="color:#d1d5db;text-decoration:none;margin-right:14px;">✉ support@vamory.vadaevri.com</a>
                <a href="https://vamory.vadaevri.com" style="color:#d1d5db;text-decoration:none;">🌐 vamory.vadaevri.com</a>
              </div>
              <div style="color:#9ca3af;font-size:13px;">© 2025 Vamory</div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>

    """
    content = content.replace("{{name}}", name)
    
    # Track email send (non-blocking)
    try:
        await track_email_send(
            email_type=EmailType.CREDITS_EXPIRED,
            to_email=toemail,
            user_name=name,
            subject=subject
        )
    except Exception as e:
        print(f"Warning: Failed to track credits expired email: {e}")
    
    return await send(to=toemail, subject=subject, content=content)

if __name__ == "__main__":
    import asyncio
    from app.database import connect_to_mongo, close_mongo_connection

    # Example usage for testing
    to = "vamit2damor@gmail.com"

    async def main():
        # Connect to database first
        await connect_to_mongo()
        print("Database connected")
        
        # Initialize email tracking
        await initialize_email_tracking()
        
        # Test the email function
        result = await send_low_credit_message(to, "Test User", "9")
        print(result)
        
        # Close database connection
        await close_mongo_connection()

    asyncio.run(main())

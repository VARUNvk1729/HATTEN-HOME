
import ast
if not hasattr(ast, "Str"):
    class _Str(ast.Constant):
        def __init__(self, s):
            super().__init__(value=s)
        @property
        def s(self):
            return self.value
    ast.Str = _Str

import os
from pathlib import Path


import pkgutil
if not hasattr(pkgutil, "get_loader"):
    pkgutil.get_loader = lambda name: None

from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from datetime import datetime
import uuid
import requests  # For Telegram notification

BASE_DIR = Path(__file__).resolve().parent

# Flask setup
app = Flask("app")


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file(BASE_DIR / ".env")


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


app.config['SECRET_KEY'] = require_env('SECRET_KEY')
# Admin config
app.config['ADMIN_USERNAME'] = require_env('ADMIN_USERNAME')
app.config['ADMIN_PASSWORD'] = require_env('ADMIN_PASSWORD')
app.config['SQLALCHEMY_DATABASE_URI'] = require_env('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Upload folders
UPLOAD_FOLDER = BASE_DIR / 'static' / 'uploads'
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)

# Secure Aadhaar upload folder (outside static)
AADHAAR_UPLOAD_FOLDER = BASE_DIR / 'uploads' / 'aadhaar'
AADHAAR_UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
app.config['AADHAAR_UPLOAD_FOLDER'] = str(AADHAAR_UPLOAD_FOLDER)

TELEGRAM_BOT_TOKEN = require_env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = require_env('TELEGRAM_CHAT_ID')

db = SQLAlchemy(app)

# Booking model
class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    confirmation_token = db.Column(db.String(64), unique=True, index=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    mobile_number = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    aadhaar_number = db.Column(db.String(12), nullable=False)
    checkin_date = db.Column(db.Date, nullable=False)
    checkout_date = db.Column(db.Date, nullable=False)
    guests = db.Column(db.Integer, default=2)
    amount = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AadhaarFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    path = db.Column(db.String(512), nullable=False)
    booking_id = db.Column(db.Integer, db.ForeignKey('booking.id'), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    booking = db.relationship('Booking', backref=db.backref('aadhaar_file', lazy=True, uselist=False))

# Admin login page
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == "POST":
        if request.form.get("username") == app.config["ADMIN_USERNAME"] and request.form.get("password") == app.config["ADMIN_PASSWORD"]:
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Invalid credentials", "danger")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated_function

# Minimal admin dashboard (placeholder)
@app.route("/admin")
@admin_required
def admin_dashboard():
    bookings = Booking.query.order_by(Booking.created_at.desc()).all()
    return render_template("admin_dashboard.html", bookings=bookings)

# Home page route
@app.route('/')
def index():
    return render_template('index.html')

# Booking step 1: form
@app.route('/book', methods=['GET', 'POST'])
def book():
    if request.method == 'POST':
        # Save form data to session
        session_data = dict(request.form)
        import uuid

        aadhaar_file = request.files.get('aadhaar_photo')
        ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'pdf'}
        MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

        def allowed_file(filename):
            return '.' in filename and \
                   filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

        # Validate file
        if aadhaar_file and allowed_file(aadhaar_file.filename):
            aadhaar_file.seek(0, os.SEEK_END)
            file_size = aadhaar_file.tell()
            aadhaar_file.seek(0)
            if file_size > MAX_FILE_SIZE:
                flash("Aadhaar file size must be under 5MB.", "danger")
                return redirect(url_for('book'))
            file_ext = os.path.splitext(aadhaar_file.filename)[1].lower()
            import time, random
            timestamp = int(time.time())
            rnd = random.randint(1, 99999999)
            unique_filename = f"{timestamp}_{rnd}_{secure_filename(aadhaar_file.filename)}"
            tmp_dir = os.path.join(app.config['AADHAAR_UPLOAD_FOLDER'], 'tmp')
            os.makedirs(tmp_dir, exist_ok=True)
            tmp_path = os.path.join(tmp_dir, unique_filename)
            aadhaar_file.save(tmp_path)
        else:
            flash("A valid Aadhaar document (jpg/jpeg/png/pdf) is required.", "danger")
            return redirect(url_for('book'))

        # Aadhaar number validation (must be 12 digits)
        aadhaar_number = session_data.get('aadhaar_number', '')
        import re
        if not aadhaar_number.isdigit() or len(aadhaar_number) != 12:
            flash("Aadhaar number must be exactly 12 digits.", "danger")
            return redirect(url_for('book'))

        # Email validation
        email = session_data.get('email', '')
        EMAIL_REGEX = r"^[A-Za-z0-9\._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
        if not re.match(EMAIL_REGEX, email):
            flash("Please provide a valid email address.", "danger")
            return redirect(url_for('book'))

        # Adults & Children parsing/validation
        adults_str = session_data.get('adults', '1')
        children_count_str = session_data.get('children_count', '0')
        children_ages_list = request.form.getlist('children_ages[]')

        try:
            adults = int(adults_str)
            if adults < 1 or adults > 10:
                raise ValueError
        except (ValueError, TypeError):
            flash("Number of adults must be 1-10.", "danger")
            return redirect(url_for('book'))

        try:
            children_count = int(children_count_str)
            if children_count < 0 or children_count > 10:
                raise ValueError
        except (ValueError, TypeError):
            flash("Number of children must be 0-10.", "danger")
            return redirect(url_for('book'))

        if len(children_ages_list) != children_count:
            flash("Please enter age for each child.", "danger")
            return redirect(url_for('book'))
        try:
            children_ages = [int(age) for age in children_ages_list] if children_count > 0 else []
            for age in children_ages:
                if age < 0 or age > 17:
                    raise ValueError
        except (ValueError, TypeError):
            flash("Invalid age entered for children.", "danger")
            return redirect(url_for('book'))

        # Calculate "chargeable guests": adults + children aged 10+
        chargeable_guests = adults + sum(1 for age in children_ages if age >= 10)
        extra_guests = max(0, chargeable_guests - 5)
        per_day_amount = 3900
        nights = 1
        try:
            nights = (datetime.strptime(session_data.get('checkout_date',''),'%Y-%m-%d').date() - 
                      datetime.strptime(session_data.get('checkin_date',''),'%Y-%m-%d').date()).days
        except Exception:
            nights = 1
        extra_charge = extra_guests * 400 * nights
        base_amount = per_day_amount * nights
        total_amount = base_amount + extra_charge
        calculation_breakdown = {
            "adults": adults,
            "children_ages": children_ages,
            "chargeable_guests": chargeable_guests,
            "extra_guests": extra_guests,
            "base_amount": base_amount,
            "extra_charge": extra_charge,
            "total_amount": total_amount,
            "per_day_amount": per_day_amount,
            "nights": nights
        }

        # Date validations (DATE ONLY)
        from datetime import date as dt_date, datetime as dt_mod

        checkin_date = session_data.get('checkin_date', '')
        checkout_date = session_data.get('checkout_date', '')
        try:
            checkin_dt = dt_mod.strptime(checkin_date, '%Y-%m-%d').date()
            checkout_dt = dt_mod.strptime(checkout_date, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            flash("Please provide valid check-in and check-out dates.", "danger")
            return redirect(url_for('book'))

        today = dt_date.today()
        if checkin_dt < today:
            flash("Check-in cannot be in the past.", "danger")
            return redirect(url_for('book'))
        if checkout_dt <= checkin_dt:
            flash("Check-out must be after check-in date.", "danger")
            return redirect(url_for('book'))

        nights = (checkout_dt - checkin_dt).days
        if nights <= 0:
            flash("Minimum stay is 1 night.", "danger")
            return redirect(url_for('book'))

        per_day_amount = 3900
        total_amount = per_day_amount * nights

        # Instead of saving to DB now, store all details in session for review step (DATE ONLY)
        session['pending_booking'] = {
            "full_name": session_data.get('full_name', ''),
            "mobile_number": session_data.get('mobile_number', ''),
            "email": email,
            "aadhaar_number": aadhaar_number,
            "checkin_date": checkin_date,
            "checkout_date": checkout_date,
            "adults": adults,
            "children_count": children_count,
            "children_ages": children_ages,
            "chargeable_guests": chargeable_guests,
            "extra_guests": extra_guests,
            "amount": total_amount,
            "base_amount": base_amount,
            "extra_charge": extra_charge,
            "per_day_amount": per_day_amount,
            "nights": nights,
            "calculation_breakdown": calculation_breakdown,
            # Store tmp info in session—do NOT commit DB or final file yet
            "aadhaar_file_tmp": unique_filename
        }
        # Redirect user to review step
        return redirect(url_for('review'))
    # On GET, prefill form if session data is present
    form_data = session.get('pending_booking')
    return render_template('book.html', booking=form_data)

# Review booking step
@app.route('/review', methods=['GET', 'POST'])
def review():
    pending = session.get('pending_booking')
    if not pending:
        flash("No booking data to review.", "danger")
        return redirect(url_for('book'))

    if request.method == 'POST':
        # Save booking and file record to DB
        # Double-check amount and date in /review confirm (DATE ONLY)
        from datetime import datetime as dt_mod
        import shutil

        checkin_dt = dt_mod.strptime(pending.get('checkin_date', ''), '%Y-%m-%d').date()
        checkout_dt = dt_mod.strptime(pending.get('checkout_date', ''), '%Y-%m-%d').date()
        nights = (checkout_dt - checkin_dt).days
        per_day_amount = 3900
        # REPEAT THE SAME CHARGE LOGIC AS IN /BOOK, using values from pending or recomputing if needed
        adults = int(pending.get('adults', 1))
        children_ages = pending.get('children_ages', [])
        if not isinstance(children_ages, list):
            try:
                children_ages = list(children_ages)
            except Exception:
                children_ages = []
        children_ages = [int(age) for age in children_ages]
        chargeable_guests = adults + sum(1 for age in children_ages if age >= 10)
        extra_guests = max(0, chargeable_guests - 5)
        base_amount = per_day_amount * nights
        extra_charge = extra_guests * 400 * nights
        total_amount = base_amount + extra_charge

        # Move Aadhaar file from tmp to final location
        aadhaar_file_tmp = pending.get('aadhaar_file_tmp')
        tmp_path = os.path.join(app.config['AADHAAR_UPLOAD_FOLDER'], 'tmp', aadhaar_file_tmp)
        final_unique_filename = f"{uuid.uuid4().hex}_{aadhaar_file_tmp.split('_', 1)[-1]}"
        final_path = os.path.join(app.config['AADHAAR_UPLOAD_FOLDER'], final_unique_filename)
        if os.path.isfile(tmp_path):
            shutil.move(tmp_path, final_path)
        else:
            flash("Aadhaar file missing. Please re-upload.", "danger")
            return redirect(url_for('book'))

        import secrets
        confirmation_token = secrets.token_urlsafe(20)
        booking = Booking(
            confirmation_token=confirmation_token,
            full_name=pending.get('full_name', ''),
            mobile_number=pending.get('mobile_number', ''),
            email=pending.get('email', ''),
            aadhaar_number=pending.get('aadhaar_number', ''),
            checkin_date=checkin_dt,
            checkout_date=checkout_dt,
            guests=chargeable_guests,
            amount=total_amount,
        )
        db.session.add(booking)
        db.session.commit()

        aadhaar_record = AadhaarFile(
            path=final_unique_filename,
            booking_id=booking.id,
        )
        db.session.add(aadhaar_record)
        db.session.commit()

        # Attach adults/children info for Telegram notification
        booking.adults = adults
        booking.children_ages = children_ages

        # Clean up session data
        session.pop('pending_booking', None)
        # Telegram notification (after DB save)
        try:
            full_aadhaar_path = os.path.join(app.config['AADHAAR_UPLOAD_FOLDER'], final_unique_filename)
            send_booking_notification_telegram(booking, full_aadhaar_path)
        except Exception as notif_err:
            print("Failed to send Telegram notification:", notif_err)
        return redirect(url_for('confirmation', confirmation_token=confirmation_token))

    return render_template('review.html', booking=pending)

# Booking confirmation page
@app.route('/confirmation/<confirmation_token>')
def confirmation(confirmation_token):
    from datetime import datetime, timedelta

    booking = Booking.query.filter_by(confirmation_token=confirmation_token).first_or_404()
    now = datetime.utcnow()
    # Expires 1 hour after "created_at"
    if booking.created_at < now - timedelta(hours=1):
        flash("This confirmation link has expired.", "warning")
        return redirect(url_for('index'))
    return render_template('confirmation.html', booking=booking)

# Secure Aadhaar file download/view route for admins
@app.route('/admin/aadhaar/<int:booking_id>')
@admin_required
def admin_aadhaar_file(booking_id):
    record = AadhaarFile.query.filter_by(booking_id=booking_id).first()
    if not record:
        flash("Aadhaar file not found for this booking.", "danger")
        return redirect(url_for("admin_dashboard"))
    filepath = os.path.join(app.config['AADHAAR_UPLOAD_FOLDER'], record.path)
    if not os.path.isfile(filepath):
        flash("Aadhaar file is missing from disk.", "danger")
        return redirect(url_for("admin_dashboard"))
    return send_file(filepath, as_attachment=True)

# Serve uploaded files
# Route removed for security: Aadhaar documents are only viewable by admins.

# Telegram Bot config

def send_booking_notification_telegram(booking, aadhaar_file_path):
    """
    Sends booking info & Aadhaar file to Telegram admin.
    """
    import os

    # Compose message text
    try:
        _pending = getattr(booking, "pending_booking", None)
    except Exception:
        _pending = None
    # Guest/children counts for message
    adults = getattr(booking, 'adults', None)
    children_ages = getattr(booking, "children_ages", [])
    if children_ages is None:
        children_ages = []
    children_count = len(children_ages)
    guest_count = getattr(booking, 'guests', 'N/A')
    msg = (
        f"🛎️ *New Booking Received!*\n"
        f"*Guest Name:* {booking.full_name}\n"
        f"*Phone Number:* {booking.mobile_number}\n"
        f"*Adults:* {adults}\n"
        f"*Children:* {children_count}\n"
        f"*Guest Count (Chargeable):* {guest_count}\n"
        f"*Check-in:* {booking.checkin_date.strftime('%Y-%m-%d')}\n"
        f"*Check-out:* {booking.checkout_date.strftime('%Y-%m-%d')}\n"
        f"*Total Amount:* ₹{booking.amount}\n"
    )
    # Send text message
    try:
        send_message_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown"
        }
        resp = requests.post(send_message_url, data=payload, timeout=8)
        resp.raise_for_status()
    except Exception as e:
        print("Telegram message sending failed:", str(e))

    # Send Aadhaar file as document (if present)
    try:
        send_doc_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
        if aadhaar_file_path and os.path.isfile(aadhaar_file_path):
            with open(aadhaar_file_path, "rb") as docf:
                data = {
                    "chat_id": TELEGRAM_CHAT_ID
                }
                files = {
                    "document": docf
                }
                resp_doc = requests.post(send_doc_url, data=data, files=files, timeout=8)
                resp_doc.raise_for_status()
    except Exception as e:
        print("Telegram document sending failed:", str(e))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true')

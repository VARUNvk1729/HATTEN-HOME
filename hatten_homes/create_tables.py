from app import app, db

# Run this script to ensure all required database tables are present.
# It will create any missing tables as defined in app.py models (Booking, AadhaarFile, etc).
# Usage: python hatten_homes/create_tables.py

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print("All tables created successfully or already present.")
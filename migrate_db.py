from sqlalchemy import create_engine, text
from database import SQLALCHEMY_DATABASE_URL

engine = create_engine(SQLALCHEMY_DATABASE_URL)

def migrate():
    with engine.connect() as conn:
        print("Starting migrations...")
        
        # Add owner_id to company_profile
        try:
            conn.execute(text("ALTER TABLE company_profile ADD COLUMN owner_id INT NULL"))
            conn.execute(text("ALTER TABLE company_profile ADD CONSTRAINT fk_company_owner FOREIGN KEY (owner_id) REFERENCES users(id)"))
            print("Successfully migrated company_profile")
        except Exception as e:
            print(f"Skipping company_profile: {e}")

        # Add owner_id to clients
        try:
            conn.execute(text("ALTER TABLE clients ADD COLUMN owner_id INT NULL"))
            conn.execute(text("ALTER TABLE clients ADD CONSTRAINT fk_client_owner FOREIGN KEY (owner_id) REFERENCES users(id)"))
            print("Successfully migrated clients")
        except Exception as e:
            print(f"Skipping clients: {e}")

        # Add owner_id to invoices
        try:
            conn.execute(text("ALTER TABLE invoices ADD COLUMN owner_id INT NULL"))
            conn.execute(text("ALTER TABLE invoices ADD CONSTRAINT fk_invoice_owner FOREIGN KEY (owner_id) REFERENCES users(id)"))
            print("Successfully migrated invoices")
        except Exception as e:
            print(f"Skipping invoices: {e}")

        conn.commit()
        print("Migrations complete!")

if __name__ == "__main__":
    migrate()

from fpdf import FPDF
from datetime import datetime

class InvoicePDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 22)
        self.set_text_color(22, 160, 133) # Professional Teal
        self.cell(0, 10, "DK DEVELOPERS", ln=True)
        self.set_font("Helvetica", "I", 10)
        self.set_text_color(128, 128, 128)
        self.cell(0, 5, "High-End Web & App Solutions", ln=True)
        self.ln(10)

    def generate(self, data, filename):
        self.add_page()
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(0)
        self.cell(0, 10, f"INVOICE TO: {data['client_name'].upper()}", ln=True)
        self.set_font("Helvetica", "", 10)
        self.cell(0, 5, f"Date: {datetime.now().strftime('%d %b, %Y')}", ln=True)
        self.ln(5)

        # Table Header
        self.set_fill_color(22, 160, 133)
        self.set_text_color(255)
        self.cell(140, 10, " Service Details", border=1, fill=True)
        self.cell(50, 10, " Amount (INR)", border=1, fill=True, ln=True)

        # Body
        self.set_text_color(0)
        for i, point in enumerate(data['points']):
            self.cell(140, 10, f" - {point}", border=1)
            price = f"Rs {data['amount']}" if i == 0 else ""
            self.cell(50, 10, f" {price}", border=1, ln=True)

        self.ln(5)
        self.set_font("Helvetica", "B", 12)
        self.cell(140, 10, " TOTAL PAYABLE", border=0, align="R")
        self.cell(50, 10, f" Rs {data['amount']}", border=1, ln=True, align="C")
        self.output(filename)
from pathlib import Path

def extract_invoice_placeholder(file_path: Path) -> dict:
    return {
        "supplier": "",
        "supplier_tax_id": "",
        "invoice_number": "",
        "invoice_date": "",
        "currency": "ARS",
        "net_amount": 0,
        "tax_amount": 0,
        "total_amount": 0,
        "items": [],
    }

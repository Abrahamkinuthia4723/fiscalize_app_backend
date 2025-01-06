from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
import os
import random
import requests
import qrcode
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fiscal_invoices.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/qr_codes'

db = SQLAlchemy(app)
migrate = Migrate(app, db)
CORS(app)

# Fiscal device API URL (mock or real)
FISCAL_DEVICE_API_URL = os.getenv('FISCAL_DEVICE_API_URL', 'http://localhost:5000/fiscalize')

class DBoInvnum(db.Model):
    __tablename__ = 'dbo_invnum'

    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(100), nullable=False)
    customer_name = db.Column(db.String(255), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False)

    invlines = db.relationship('Invlines', backref='invoice', lazy=True)


class Invlines(db.Model):
    __tablename__ = 'invlines'

    id = db.Column(db.Integer, primary_key=True)
    invid = db.Column(db.Integer, db.ForeignKey('dbo_invnum.id'), nullable=False)
    item_name = db.Column(db.String(255), nullable=False)
    item_code = db.Column(db.String(100), nullable=True)
    description = db.Column(db.String(255), nullable=True)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    tax_rate = db.Column(db.Float, nullable=True)
    discount = db.Column(db.Float, nullable=True)


class FiscalData(db.Model):
    __tablename__ = 'fiscal_data'

    id = db.Column(db.Integer, primary_key=True)
    invid = db.Column(db.Integer, db.ForeignKey('dbo_invnum.id'), nullable=False)
    qr_code_path = db.Column(db.String(255), nullable=False)
    signature = db.Column(db.String(255), nullable=False)


@app.route('/invoices', methods=['GET'])
def get_today_invoices():
    """Fetch all invoices created today with their line items."""
    today = datetime.now().date()
    invoices = DBoInvnum.query.filter(db.func.date(DBoInvnum.created_at) == today).all()

    if not invoices:
        return jsonify({'message': 'No invoices found for today.'}), 404

    invoices_data = []
    for invoice in invoices:
        items = Invlines.query.filter_by(invid=invoice.id).all()
        item_objects = [
            {
                'item_name': item.item_name,
                'quantity': item.quantity,
                'price': item.price,
                'total_price': item.quantity * item.price,
                'tax_rate': item.tax_rate,
                'discount': item.discount
            }
            for item in items
        ]
        invoice_data = {
            'id': invoice.id,
            'invoice_number': invoice.invoice_number,
            'customer_name': invoice.customer_name,
            'total_amount': invoice.total_amount,
            'created_at': invoice.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'items': item_objects  
        }
        invoices_data.append(invoice_data)

    return jsonify(invoices_data)


def generate_qr_code(invoice):
    """Generate a QR code for the invoice and save it to the static folder."""
    
    invoice_details = f"""
    Invoice Number: {invoice.invoice_number}
    Customer: {invoice.customer_name}
    Total Amount: {invoice.total_amount}
    Date: {invoice.created_at.strftime('%Y-%m-%d %H:%M:%S')}
    """
    
    # Create a QR code with the invoice details
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    
    qr.add_data(invoice_details)
    qr.make(fit=True)

    # Create an image from the QR code
    img = qr.make_image(fill='black', back_color='white')

    # Save the QR code to the static/qr_codes directory
    qr_code_path = f"static/qr_codes/invoice_{invoice.id}.png"  
    img.save(qr_code_path)

    return qr_code_path


def process_invoice(invoice):
    """Communicate with the fiscal device to fiscalize an invoice."""
    # Check if fiscal data already exists for the invoice
    existing_fiscal_data = FiscalData.query.filter_by(invid=invoice.id).first()
    if existing_fiscal_data:
        print(f"Fiscal data for invoice {invoice.id} already exists. Skipping.")
        return

    items = Invlines.query.filter_by(invid=invoice.id).all()
    item_objects = [
        {
            'item_name': item.item_name,
            'item_code': item.item_code,
            'description': item.description,
            'quantity': item.quantity,
            'price': item.price,
            'total_price': item.quantity * item.price,
            'tax_rate': item.tax_rate,
            'discount': item.discount,
        }
        for item in items
    ]

    # Generate the QR code for the invoice
    qr_code_path = generate_qr_code(invoice)
    signature = f"Signature-{random.randint(1000, 9999)}"

    # Store the fiscal data in the database
    fiscal_data = FiscalData(invid=invoice.id, qr_code_path=qr_code_path, signature=signature)
    db.session.add(fiscal_data)
    db.session.commit()

    # If the real fiscal device URL is set, it's called; otherwise, mock is used
    if FISCAL_DEVICE_API_URL != 'http://localhost:5000/fiscalize':
        fiscalize_with_real_device(invoice)
    else:
        fiscalize_with_mock_device(invoice)


def fiscalize_with_real_device(invoice):
    """Function to call the real fiscal device API."""
    items = Invlines.query.filter_by(invid=invoice.id).all()
    item_objects = [
        {
            'item_name': item.item_name,
            'item_code': item.item_code,
            'description': item.description,
            'quantity': item.quantity,
            'price': item.price,
            'total_price': item.quantity * item.price,
            'tax_rate': item.tax_rate,
            'discount': item.discount,
        }
        for item in items
    ]

    # Prepare payload for the real fiscal device API
    payload = {
        "invoice_id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "customer_name": invoice.customer_name,
        "total_amount": invoice.total_amount,
        "items": item_objects,
    }

    try:
        response = requests.post(FISCAL_DEVICE_API_URL, json=payload, timeout=10)
        response.raise_for_status()

        response_data = response.json()
        qr_code_url = response_data.get("qr_code_url")
        signature = response_data.get("signature")

        if not qr_code_url or not signature:
            raise ValueError("Invalid response from fiscal device. Missing QR code or signature.")

        fiscal_data = FiscalData(invid=invoice.id, qr_code_path=qr_code_url, signature=signature)
        db.session.add(fiscal_data)
        db.session.commit()

    except requests.exceptions.RequestException as e:
        print(f"Error with fiscal device communication: {e}")
        raise Exception(f"Fiscal device error: {e}")


def fiscalize_with_mock_device(invoice):
    """Mock fiscal device endpoint."""
    data = {
        "invoice_id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "customer_name": invoice.customer_name,
        "total_amount": invoice.total_amount,
    }

    qr_code_path = f"static/qr_codes/invoice_{invoice.id}.png" 
    signature = f"Signature-{random.randint(1000, 9999)}"

    # Store the fiscal data in the database
    fiscal_data = FiscalData(invid=invoice.id, qr_code_path=qr_code_path, signature=signature)
    db.session.add(fiscal_data)
    db.session.commit()

    print(f"Fiscal data for invoice {invoice.id} processed successfully.")


@app.route('/process_invoices', methods=['POST'])
def process_selected_invoices():
    """Fiscalize selected invoices."""
    try:
        data = request.get_json()
        selected_invoices_ids = data.get('selected_invoices', [])

        if not selected_invoices_ids:
            return jsonify({'error': 'No invoices selected'}), 400

        invoices = DBoInvnum.query.filter(DBoInvnum.id.in_(selected_invoices_ids)).all()

        if not invoices:
            return jsonify({'error': 'Invoices not found'}), 404

        for invoice in invoices:
            process_invoice(invoice)

        return jsonify({'message': 'Invoices successfully fiscalized'}), 200

    except Exception as e:
        print(f"Error processing invoices: {e}")
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)

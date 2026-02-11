# -*- coding: utf-8 -*-
from odoo import http, fields
from odoo.http import request
import logging
import json

_logger = logging.getLogger(__name__)


class CartAPI(http.Controller):

    def _log_event(self, event, client_ip, **kwargs):
        """Structured logging helper."""
        log_data = {
            'timestamp': fields.Datetime.now(),
            'event': event,
            'ip': client_ip,
            **kwargs
        }
        _logger.info(json.dumps(log_data, default=str))

    @http.route('/api/v1/cart/add', type='json', auth="public", website=True, csrf=False, methods=['POST'], cors="*")
    def add_to_cart(self, **kwargs):
        """
        Adds a product to the cart.
        Handles both strict JSON-RPC and standard JSON POST bodies.
        """
        try:
            # 1. Bulletproof Data Extraction
            # Odoo's @http.route(type='json') usually puts the 'params' content into kwargs.
            # If kwargs is empty, we manually pull from the raw JSON body.
            if not kwargs:
                raw_data = request.get_json_data()
                # Handle cases where the user sends {"params": {...}} or just {...}
                data = raw_data.get('params', raw_data) if isinstance(raw_data, dict) else {}
            else:
                data = kwargs

            client_ip = request.httprequest.remote_addr
            product_id_raw = data.get('product_id')
            quantity_raw = data.get('quantity', 1)

            # 2. Validation Guard Clauses
            if product_id_raw is None:
                return {
                    'status': 'error',
                    'message': "Missing 'product_id'. Ensure your JSON body contains 'product_id' inside the 'params' object."
                }

            try:
                product_id = int(product_id_raw)
                quantity = float(quantity_raw)
            except (ValueError, TypeError):
                return {
                    'status': 'error',
                    'message': "Invalid data format. 'product_id' must be an integer and 'quantity' a number."
                }

            # 3. Product Verification
            # We use sudo() to ensure the product is readable even for public/new sessions
            product = request.env['product.product'].sudo().browse(product_id)
            if not product.exists() or not product.active:
                return {'status': 'error', 'message': f"Product ID {product_id} does not exist or is archived."}

            # 4. Cart Operation
            # sale_get_order(force_create=True) handles the session-to-order mapping in Odoo
            sale_order = request.website.sale_get_order(force_create=True)

            # _cart_update is the standard Odoo method for adding/removing/updating items
            # It handles pricelists, taxes, and constraints automatically.
            sale_order._cart_update(
                product_id=product.id,
                add_qty=quantity
            )

            # 5. Build Comprehensive Response
            is_confirmed = sale_order.state in ['sale', 'done']

            items = [{
                'line_id': line.id,
                'product_id': line.product_id.id,
                'product_name': line.product_id.name,
                'quantity': line.product_uom_qty,
                'price_unit': line.price_unit,
                'tax_amount': line.price_tax,
                'subtotal': line.price_subtotal,
                'total': line.price_total,
            } for line in sale_order.order_line]

            response_payload = {
                'status': 'success',
                'order_overview': {
                    'cart_id': sale_order.id,
                    'status': 'Confirmed Order' if is_confirmed else 'Draft Quotation',
                    'order_reference': sale_order.name,
                    'currency': sale_order.currency_id.name,
                    'summary': {
                        'untaxed_amount': sale_order.amount_untaxed,
                        'tax_amount': sale_order.amount_tax,
                        'total_amount': sale_order.amount_total,
                        'items_count': int(sale_order.cart_quantity)  # Built-in Odoo field
                    },
                    'items': items
                }
            }

            # Log the successful interaction
            self._log_event('cart_add_success', client_ip, product_id=product_id, qty=quantity)

            return response_payload

        except Exception as e:
            # Capture the full traceback in Odoo logs for debugging
            _logger.error(f"Cart API Fatal Error: {str(e)}", exc_info=True)
            return {
                'status': 'error',
                'message': "Internal server error. Check Odoo logs for details."
            }
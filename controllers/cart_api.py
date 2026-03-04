# -*- coding: utf-8 -*-
from odoo import http, fields
from odoo.http import request, Response
import logging
import json

_logger = logging.getLogger(__name__)

class CartAPI(http.Controller):

    def _log_event(self, event, client_ip, **kwargs):
        log_data = {
            'timestamp': fields.Datetime.now(),
            'event': event,
            'ip': client_ip,
            **kwargs
        }
        _logger.info(json.dumps(log_data, default=str))

    @http.route(
        '/api/v1/cart/add',
        type='http',                # ← changed here
        auth="public",
        website=True,
        csrf=False,
        methods=['POST'],
        cors="https://your-frontend-domain.com"  # ← still strongly recommended (never "*")
    )
    def add_to_cart(self, **kwargs):
        """
        Adds a product to the cart – now using type='http'
        """
        try:
            client_ip = request.httprequest.remote_addr

            # ── Input parsing: more manual now ───────────────────────────────
            content_type = request.httprequest.headers.get('Content-Type', '').lower()

            if 'application/json' in content_type:
                # Client sent JSON → parse it
                try:
                    data = request.get_json_data()          # or json.loads(request.httprequest.data)
                except Exception:
                    return self._json_error_response(
                        "Invalid JSON body", code=400, error_type="bad_request"
                    )
            else:
                # Fallback: form data (application/x-www-form-urlencoded or multipart)
                data = request.params

            # Extract fields (same logic as before)
            product_id_raw = data.get('product_id')
            quantity_raw = data.get('quantity', 1)

            if product_id_raw is None:
                return self._json_error_response(
                    "Missing 'product_id'", code=400, error_type="validation_error"
                )

            try:
                product_id = int(product_id_raw)
                quantity = float(quantity_raw)
            except (ValueError, TypeError):
                return self._json_error_response(
                    "Invalid format: 'product_id' must be integer, 'quantity' a number",
                    code=400
                )

            # Product check
            product = request.env['product.product'].sudo().browse(product_id)
            if not product.exists() or not product.active or not product.sale_ok:
                return self._json_error_response(
                    f"Product ID {product_id} not found or not available", code=404
                )

            # Cart logic (unchanged)
            sale_order = request.website.sale_get_order(force_create=True)
            sale_order._cart_update(
                product_id=product.id,
                add_qty=quantity
            )

            # Build same rich payload
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

            payload = {
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
                        'items_count': int(sale_order.cart_quantity)
                    },
                    'items': items
                }
            }

            self._log_event('cart_add_success', client_ip,
                           product_id=product_id, qty=quantity, order_id=sale_order.id)

            # ── Return proper HTTP response ────────────────────────────────
            return request.make_response(
                json.dumps(payload, default=str),
                headers=[('Content-Type', 'application/json')],
                status=200
            )

        except Exception as e:
            _logger.error(f"Cart API Fatal Error: {str(e)}", exc_info=True)
            return self._json_error_response(
                "Internal server error. Contact support.", code=500
            )

    def _json_error_response(self, message, code=400, error_type="error"):
        """Helper to return consistent JSON + correct HTTP status"""
        payload = {
            'status': 'error',
            'code': error_type,
            'message': message
        }
        return request.make_response(
            json.dumps(payload, default=str),
            headers=[('Content-Type', 'application/json')],
            status=code
        )
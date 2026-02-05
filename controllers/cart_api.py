# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError
import logging
import datetime
import json

_logger = logging.getLogger(__name__)

class CartAPI(http.Controller):

    def _log_event(self, event, client_ip, request_time, **kwargs):
        """Structured logging helper."""
        log_data = {
            'timestamp': request_time,
            'event': event,
            'ip': client_ip,
            **kwargs
        }
        _logger.info(json.dumps(log_data, default=str))

    @http.route('/api/v1/cart/add', type='json', auth="public", website=True, csrf=False, methods=['POST'], cors="*")
    def add_to_cart(self, **params):
        try:
            product_id = int(params.get('product_id'))
            quantity = float(params.get('quantity', 1))

            # Get or create the current draft order (Quotation)
            sale_order = request.website.sale_get_order(force_create=True)

            # Standard Odoo cart update logic
            sale_order._cart_update(
                product_id=product_id,
                add_qty=quantity
            )

            # Build the Order Overview
            items = [{
                'product_id': line.product_id.id,
                'product_name': line.product_id.name,
                'quantity': line.product_uom_qty,
                'price_unit': line.price_unit,
                'subtotal': line.price_subtotal,
                'total': line.price_total,
            } for line in sale_order.order_line]

            # Logic: Only provide the 'Order Reference' if the state is 'sale' or 'done'
            # Otherwise, we label it as a 'Quotation' or 'Draft'
            is_confirmed = sale_order.state in ['sale', 'done']

            return {
                'status': 'success',
                'order_overview': {
                    'cart_id': sale_order.id,
                    'status': 'Confirmed Order' if is_confirmed else 'Draft Quotation',
                    'order_reference': sale_order.name if is_confirmed else "Pending Confirmation",
                    'quotation_reference': sale_order.name if not is_confirmed else None,
                    'currency': sale_order.currency_id.name,
                    'summary': {
                        'untaxed_amount': sale_order.amount_untaxed,
                        'tax_amount': sale_order.amount_tax,
                        'total_amount': sale_order.amount_total,
                        'items_count': int(sum(sale_order.mapped('order_line.product_uom_qty')))
                    },
                    'items': items
                }
            }

        except Exception as e:
            _logger.error(f"Cart API Error: {str(e)}")
            return {'status': 'error', 'message': "Could not update cart."}
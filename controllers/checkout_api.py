# -*- coding: utf-8 -*-
from odoo import http, _
from odoo.http import request
import json
import logging

_logger = logging.getLogger(__name__)


class OneStepCheckout(http.Controller):

    @http.route(
        ['/api/v1/checkout'],
        type='http',
        auth="public",
        website=True,
        csrf=False,
        methods=['POST'],
        cors="*"
    )
    def one_step_submit(self, **kwargs):
        """
        One-step checkout API – now correctly handles JSON body.

        Frontend must send:
        Content-Type: application/json
        body: JSON.stringify({ name, email, street, city, country_id, ... })

        Returns JSON response with redirect to payment on success.
        """
        order = request.website.sale_get_order(force_create=False)

        if not order or not order.order_line:
            return request.make_response(
                json.dumps({'status': 'error', 'message': 'Cart is empty'}),
                headers=[('Content-Type', 'application/json')],
                status=400
            )

        # ────────────────────────────────────────────────
        # IMPORTANT: Read and parse JSON body correctly
        # ────────────────────────────────────────────────
        payload = {}
        if request.httprequest.data:
            try:
                payload = json.loads(request.httprequest.data.decode('utf-8'))
            except json.JSONDecodeError:
                return request.make_response(
                    json.dumps({'status': 'error', 'message': 'Invalid JSON in request body'}),
                    headers=[('Content-Type', 'application/json')],
                    status=400
                )

        # Merge any query-string params + JSON body (most important part)
        post = {**dict(request.params), **payload}

        # Debug helper (remove in production if you want)
        _logger.info("Checkout API received keys: %s", list(post.keys()))

        try:
            # ────────────────────────────────────────────────
            # 1. Required fields validation
            # ────────────────────────────────────────────────
            required_fields = ['name', 'email', 'street', 'city', 'country_id']
            missing = [f for f in required_fields if not post.get(f)]

            if missing:
                return request.make_response(
                    json.dumps({
                        'status': 'error',
                        'message': f"Missing required field(s): {', '.join(missing)}",
                        'received_keys': list(post.keys())   # helps debugging
                    }),
                    headers=[('Content-Type', 'application/json')],
                    status=400
                )

            # Light email validation
            email = str(post.get('email', '')).strip()
            if '@' not in email or '.' not in email.rsplit('@', 1)[-1]:
                return request.make_response(
                    json.dumps({'status': 'error', 'message': 'Invalid email format'}),
                    headers=[('Content-Type', 'application/json')],
                    status=400
                )

            # ────────────────────────────────────────────────
            # 2. Prepare address data
            # ────────────────────────────────────────────────
            addr_values = {
                'name': str(post.get('name', '')).strip(),
                'email': email,
                'street': str(post.get('street', '')).strip(),
                'city': str(post.get('city', '')).strip(),
                'zip': str(post.get('zip', '')).strip(),
                'country_id': int(post.get('country_id')),
                'state_id': int(post.get('state_id')) if post.get('state_id') else False,
                'phone': str(post.get('phone', '')).strip(),
            }

            # ────────────────────────────────────────────────
            # 3. Update partner
            # ────────────────────────────────────────────────
            partner = order.partner_id
            partner.sudo().write(addr_values)

            # (Optional safer version for logged-in users – uncomment if needed)
            # if partner.user_ids:
            #     delivery_partner = partner.sudo().copy({
            #         **addr_values,
            #         'parent_id': partner.id,
            #         'type': 'delivery',
            #     })
            #     order.sudo().write({
            #         'partner_shipping_id': delivery_partner.id,
            #         # 'partner_invoice_id': delivery_partner.id or partner.id
            #     })

            # ────────────────────────────────────────────────
            # 4. Carrier selection
            # ────────────────────────────────────────────────
            carrier = False
            carrier_name = None
            delivery_amount = 0.0

            carrier_id_str = post.get('carrier_id')
            if carrier_id_str:
                try:
                    carrier_id = int(carrier_id_str)
                    carrier = request.env['delivery.carrier'].sudo().browse(carrier_id)
                    if carrier.exists():
                        order._check_carrier_quotation(carrier)
                        order._compute_amounts()
                        carrier_name = carrier.name
                        delivery_amount = order.amount_delivery
                except Exception as carrier_err:
                    _logger.warning("Carrier failed: %s", str(carrier_err))

            # ────────────────────────────────────────────────
            # 5. Response
            # ────────────────────────────────────────────────
            response_data = {
                'status': 'success',
                'order_id': order.id,
                'amount_untaxed': round(order.amount_untaxed, 2),
                'amount_tax': round(order.amount_tax, 2),
                'amount_delivery': round(delivery_amount, 2),
                'amount_total': round(order.amount_total, 2),
                'currency': order.currency_id.name,
                'currency_symbol': order.currency_id.symbol,
                'carrier_name': carrier_name,
                'carrier_id': carrier.id if carrier else None,
                'items_count': len(order.order_line),
                'redirect_url': '/shop/payment',
            }

            return request.make_response(
                json.dumps(response_data, default=str),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.exception("One-step checkout failed – order %s", order.id)
            return request.make_response(
                json.dumps({
                    'status': 'error',
                    'message': 'Server error during checkout. Please try again.',
                    'detail': str(e) if request.env.user.has_group('base.group_system') else None
                }),
                headers=[('Content-Type', 'application/json')],
                status=500
            )


    @http.route(
        '/shop/checkout/update_carrier',
        type='json',
        auth="public",
        website=True,
        methods=['POST']
    )
    def update_carrier_json(self, carrier_id=None):
        """AJAX helper – update carrier and return new totals"""
        order = request.website.sale_get_order()
        if not order:
            return {'status': 'error', 'message': 'No active order'}

        try:
            if not carrier_id:
                return {'status': 'error', 'message': 'carrier_id required'}

            carrier = request.env['delivery.carrier'].sudo().browse(int(carrier_id))
            if not carrier.exists():
                return {'status': 'error', 'message': 'Invalid carrier'}

            order._check_carrier_quotation(carrier)
            order._compute_amounts()

            return {
                'status': 'success',
                'amount_total': round(order.amount_total, 2),
                'amount_delivery': round(order.amount_delivery, 2),
                'currency': order.currency_id.name,
                'currency_symbol': order.currency_id.symbol,
                'carrier_name': carrier.name
            }
        except Exception as e:
            _logger.error("Carrier update failed: %s", str(e))
            return {'status': 'error', 'message': str(e)}
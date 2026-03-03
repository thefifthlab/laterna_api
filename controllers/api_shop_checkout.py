# -*- coding: utf-8 -*-
from odoo import http, _
from odoo.http import request
import logging
import json

_logger = logging.getLogger(__name__)


class ApiShopCheckout(http.Controller):
    """
    REST API for One-Step Checkout in Odoo 18.
    """

    def _get_current_website(self):
        return request.env['website'].get_current_website()

    def _prepare_partner_vals(self, values):
        """Sanitize input and map state names/ids to Odoo records."""
        vals = {
            'name': values.get('name'),
            'email': values.get('email'),
            'phone': values.get('phone'),
            'street': values.get('street'),
            'street2': values.get('street2'),
            'city': values.get('city'),
            'zip': values.get('zip'),
            'country_id': int(values.get('country_id')) if values.get('country_id') else False,
        }
        state_input = values.get('state_name') or values.get('state_id')
        if state_input and vals['country_id']:
            domain = [('country_id', '=', vals['country_id'])]
            if isinstance(state_input, str) and not str(state_input).isdigit():
                domain += ['|', ('name', '=ilike', state_input), ('code', '=ilike', state_input)]
            else:
                domain += [('id', '=', int(state_input))]
            state = request.env['res.country.state'].sudo().search(domain, limit=1)
            if state:
                vals['state_id'] = state.id
        return vals

    @http.route('/api/shop/checkout', type='json', auth='public', methods=['POST'], csrf=False, cors='*')
    def api_get_checkout_data(self, **kw):
        """
        Retrieves current cart, addresses, and available carriers.
        """
        website = self._get_current_website()
        order = website.sale_get_order(force_create=True)

        if not order:
            return {"error": "No active cart found"}

        billing = order.partner_id
        shipping = order.partner_shipping_id or billing

        # FIX FOR ODOO 18: Use _get_delivery_methods on the order instance
        carriers_data = []
        available_carriers = order._get_delivery_methods()

        for carrier in available_carriers:
            try:
                rate = carrier.rate_shipment(order)
                if rate.get('success'):
                    carriers_data.append({
                        "id": carrier.id,
                        "name": carrier.name,
                        "price": rate.get('price', 0),
                        "currency": order.currency_id.name,
                    })
            except Exception as e:
                _logger.error("Rate calculation failed for carrier %s: %s", carrier.name, str(e))

        return {
            "cart": {
                "order_id": order.id,
                "amount_total": order.amount_total,
                "currency": order.currency_id.name,
                "lines": [{"product": l.product_id.name, "qty": l.product_uom_qty, "price": l.price_total} for l in
                          order.order_line]
            },
            "billing_address": {
                "id": billing.id,
                "name": billing.name,
                "street": billing.street,
                "country_id": billing.country_id.id
            },
            "available_carriers": carriers_data,
            "available_countries": request.env['res.country'].sudo().search_read([], ['id', 'name', 'code'])
        }

    @http.route('/api/shop/address', type='json', auth='public', methods=['POST'], csrf=False, cors='*')
    def api_save_address(self, **kw):
        try:
            # 1. FORCED DATA EXTRACTION
            # This works regardless of whether Odoo thinks it's 'http' or 'json'
            if hasattr(request, 'jsonrequest'):
                data = request.jsonrequest
            else:
                # Manually parse the raw bytes from the request body
                raw_data = request.httprequest.data
                data = json.loads(raw_data).get('params', {})

            # 2. PROCEED WITH LOGIC
            order = self._get_current_website().sale_get_order(force_create=True)
            billing_data = data.get('billing', {})

            Partner = request.env['res.partner'].sudo()
            public_partner = request.env.ref('base.public_partner')

            billing_vals = self._prepare_partner_vals(billing_data)

            if order.partner_id.id == public_partner.id:
                billing_partner = Partner.create(billing_vals)
            else:
                order.partner_id.write(billing_vals)
                billing_partner = order.partner_id

            order.write({
                'partner_id': billing_partner.id,
                'partner_invoice_id': billing_partner.id,
                'partner_shipping_id': billing_partner.id,
            })

            order._recompute_prices()
            return {"success": True, "billing_id": billing_partner.id}

        except Exception as e:
            # Return a dictionary so type='json' can serialize it
            return {"success": False, "error": str(e)}

    @http.route('/api/shop/checkout/confirm', type='json', auth='public', methods=['POST'], csrf=False, cors='*')
    def api_confirm_checkout(self, **kw):
        """Assigns carrier and prepares order for payment."""
        order = self._get_current_website().sale_get_order()
        if not order or order.state != 'draft':
            return {"success": False, "error": "Invalid order state"}

        if hasattr(request, 'jsonrequest'):
            data = request.jsonrequest
        else:
            data = json.loads(request.httprequest.data).get('params', {})

        carrier_id = data.get('carrier_id')
        if carrier_id:
            carrier = request.env['delivery.carrier'].sudo().browse(int(carrier_id))
            rate = carrier.rate_shipment(order)
            if rate.get('success'):
                order.set_delivery_line(carrier, rate.get('price', 0))
                order._recompute_prices()

        # Confirm the order (converts to Sales Order)
        order.action_confirm()
        return {
            "success": True,
            "order_name": order.name,
            "payment_url": f"/shop/payment?order_id={order.id}"
        }
# -*- coding: utf-8 -*-
from odoo import http
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.http import request


class CustomWebsiteSale(WebsiteSale):

    @http.route(['/shop/checkout'], type='http', auth="public", website=True,
                sitemap=False, methods=['GET', 'POST'], csrf=False, cors="*")
    def checkout(self, **post):
        """One-step checkout: GET renders template, POST validates CSRF"""
        order = request.website.sale_get_order(force_create=True)

        if request.httprequest.method == 'GET':
            values = {
                'order': order,
                'is_one_step': True,
                'shipping_carriers': self._get_available_carriers(order) if order else [],
                'checkout_values': self._prepare_checkout_values(order) if order else {},
                'csrf_token': request.csrf_token(),
            }
            return request.render('website_sale_onestep_checkout.onestep_checkout', values)

        if request.httprequest.method == 'POST':
            submitted_token = request.params.get('csrf_token')
            if not submitted_token or not request.validate_csrf(submitted_token):
                return request.redirect('/shop/cart?csrf_error=1')
            return super(WebsiteSale, self).shop(**post)

        return super(WebsiteSale, self).shop(**post)

    # ---------------- Helper Methods ----------------

    def _get_available_carriers(self, order):
        """Fetch carriers that can deliver the order and calculate rates"""
        carriers = request.env['delivery.carrier'].sudo().search([('website_published', '=', True)])
        available = []
        for c in carriers:
            try:
                if c.available_carriers(order):  # True/False
                    rate = c.rate_shipment(order)
                    available.append({
                        'carrier': c,
                        'price': rate.get('price', 0) if rate.get('success') else 0,
                        'delivery_message': rate.get('warning_message') or rate.get('error_message') or ''
                    })
            except Exception:
                available.append({
                    'carrier': c,
                    'price': 0,
                    'delivery_message': 'Error calculating rate'
                })
        return available

    def _prepare_checkout_values(self, order):
        """Prepare billing/shipping dicts for frontend template"""
        p = order.partner_id
        s = order.partner_shipping_id or p
        f = lambda x: (x or '').strip()
        return {
            'billing': {k: f(getattr(p, k, '')) if k not in ('country_id', 'state_id') else getattr(p, k).id or False
                        for k in ('name', 'email', 'phone', 'street', 'street2', 'city', 'zip', 'country_id', 'state_id')},
            'shipping': {k: f(getattr(s, k, '')) if k not in ('country_id', 'state_id') else getattr(s, k).id or False
                         for k in ('name', 'phone', 'street', 'street2', 'city', 'zip', 'country_id', 'state_id')},
            'same_as_billing': order.partner_shipping_id.id == order.partner_id.id,
        }

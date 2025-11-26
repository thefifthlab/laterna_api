# controllers/main.py
# -*- coding: utf-8 -*-
from odoo import http
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.http import request


class CustomWebsiteSale(WebsiteSale):

    @http.route(
        ['/shop/checkout'],
        type='http',
        auth="public",
        website=True,
        sitemap=False,
        methods=['GET', 'POST'],
        csrf=False                    # ← We disable built-in check
    )
    def checkout(self, **post):
        # Always let original Odoo logic run first
        response = super(CustomWebsiteSale, self).checkout(**post)

        # ———————— GET: Render our beautiful one-step page ————————
        if request.httprequest.method == 'GET':
            if getattr(response, 'is_qweb', False):
                values = response.qcontext.copy()
                order = values.get('order')
                if order:
                    values.update({
                        'is_one_step': True,
                        'shipping_carriers': self._get_available_carriers(order),
                        'checkout_values': self._prepare_checkout_values(order),
                        # This is the magic: Odoo frontend will read this automatically
                        'csrf_token': request.csrf_token(),
                    })
                    return request.render('website_sale_onestep_checkout.onestep_checkout', values)

        # ———————— POST: Manually validate CSRF + process form ————————
        if request.httprequest.method == 'POST':
            # Manual CSRF validation (safe & clean)
            submitted_token = request.params.get('csrf_token')
            if not submitted_token or not request.validate_csrf(submitted_token):
                return request.redirect('/shop/cart?csrf_error=1')

            # Let original Odoo process addresses, shipping, etc.
            return response  # This will redirect to /shop/payment or confirm order

        return response

    # ———————————————————— Helper Methods ————————————————————

    def _get_available_carriers(self, order):
        carriers = request.env['delivery.carrier'].sudo().search([('website_published', '=', True)])
        available = carriers.available_carriers(order)
        for c in available:
            rate = c.rate_shipment(order)
            c.price = rate.get('price', 0) if rate.get('success') else 0
            c.delivery_message = rate.get('warning_message') or rate.get('error_message') or ''
        return available

    def _prepare_checkout_values(self, order):
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
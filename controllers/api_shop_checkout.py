# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request, Response
import json
from odoo.addons.website_sale.controllers.main import WebsiteSale

class ApiShopCheckout(http.Controller):

    # --------------------------------------------------
    # GET /api/shop/checkout
    # Returns current cart + pre-filled addresses
    # --------------------------------------------------
    @http.route('/api/shop/checkout', type='http', auth='public', methods=['GET'], csrf=False, cors='*')
    def get_checkout(self, **kw):
        order = request.website.sale_get_order(force_create=True)

        # Pre-fill billing & shipping from logged-in user or last used
        billing = order.partner_id
        shipping = order.partner_shipping_id or billing

        def partner_to_dict(partner):
            if not partner:
                return {}
            return {
                "id": partner.id,
                "name": partner.name or "",
                "email": partner.email or "",
                "phone": partner.phone or "",
                "street": partner.street or "",
                "street2": partner.street2 or "",
                "city": partner.city or "",
                "zip": partner.zip or "",
                "country_id": partner.country_id.id or False,
                "country_code": partner.country_id.code or "",
                "state_id": partner.state_id.id or False,
                "state_name": partner.state_id.name or "",
            }

        response = {
            "cart": {
                "order_id": order.id,
                "amount_total": order.amount_total,
                "amount_tax": order.amount_tax,
                "amount_untaxed": order.amount_untaxed,
                "currency": order.currency_id.name,
                "lines": []
            },
            "billing_address": partner_to_dict(billing),
            "shipping_address": partner_to_dict(shipping),
            "shipping_same_as_billing": order.partner_shipping_id.id == order.partner_id.id,
            "available_countries": request.env['res.country'].sudo().search_read([], ['id', 'name', 'code']),
            "available_carriers": []
        }

        # Cart lines
        for line in order.order_line:
            response["cart"]["lines"].append({
                "id": line.id,
                "product_id": line.product_id.id,
                "name": line.name,
                "quantity": line.product_uom_qty,
                "price_unit": line.price_unit,
                "price_subtotal": line.price_subtotal,
                "image_url": f"/web/image/product.product/{line.product_id.id}/image_1920" if line.product_id.image_1920 else None,
            })

        # Available delivery carriers
        carriers = request.env['delivery.carrier'].sudo().available_carriers(order)
        for carrier in carriers:
            rate = carrier.rate_shipment(order)
            response["available_carriers"].append({
                "id": carrier.id,
                "name": carrier.name,
                "price": rate['price'] if not rate.get('error_message') else 0,
                "error": rate.get('error_message'),
                "free_if": carrier.free_over and order.amount_untaxed >= carrier.amount,
            })

        return Response(json.dumps(response), headers={'Content-Type': 'application/json'})

    # --------------------------------------------------
    # POST /api/shop/address
    # Request body: JSON with billing & shipping
    # --------------------------------------------------
    @http.route('/api/shop/address', type='json', auth='public', methods=['POST'], csrf=False)
    def save_address(self, **kw):
        order = request.website.sale_get_order(force_create=True)
        data = request.jsonrequest

        required_fields = ['name', 'street', 'city', 'country_id']
        billing = data.get('billing', {})
        shipping = data.get('shipping') or billing
        same = data.get('same_as_billing', True)

        # Validate required fields
        for field in required_fields:
            if not billing.get(field):
                return {"success": False, "error": f"Billing {field} is required"}

        # Create or update billing partner
        Partner = request.env['res.partner'].sudo()
        billing_partner = Partner.create_or_update(billing)

        # Shipping
        if same:
            shipping_partner = billing_partner
        else:
            shipping_partner = Partner.create_or_update(shipping)

        # Assign to order
        order.write({
            'partner_id': billing_partner.id,
            'partner_invoice_id': billing_partner.id,
            'partner_shipping_id': shipping_partner.id,
        })

        return {
            "success": True,
            "order_id": order.id,
            "billing_address_id": billing_partner.id,
            "shipping_address_id": shipping_partner.id,
        }

    # --------------------------------------------------
    # POST /api/shop/checkout/confirm
    # Final confirmation (payment will be handled separately)
    # --------------------------------------------------
    @http.route('/api/shop/checkout/confirm', type='json', auth='public', methods=['POST'], csrf=False)
    def confirm_checkout(self, **kw):
        order = request.website.sale_get_order()
        if not order or order.state not in ('draft', 'sent'):
            return {"success": False, "error": "No valid cart"}

        # Optional: set carrier
        carrier_id = request.jsonrequest.get('carrier_id')
        if carrier_id:
            order.set_delivery_method(int(carrier_id))

        # Confirm the sale order
        order.with_context(from_api=True).action_confirm()

        # Return confirmation + next step (usually payment)
        return {
            "success": True,
            "order_id": order.id,
            "order_name": order.name,
            "amount_total": order.amount_total,
            "next_step": "payment",  # your frontend should redirect to /shop/payment
            "payment_url": f"/shop/payment/transaction/{order.id}",
        }

# Helper: create or update partner from dict
def create_or_update(self, values):
    Partner = self.env['res.partner'].sudo()
    partner = Partner
    if values.get('id'):
        partner = Partner.browse(values['id'])
        if partner.exists():
            partner.write(values)
            return partner
    # Remove id if creating new
    values.pop('id', None)
    return Partner.create(values)
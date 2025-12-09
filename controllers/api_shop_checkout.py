# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request, Response, _logger
import json
from odoo.addons.website_sale.controllers.main import WebsiteSale

class ApiShopCheckout(http.Controller):

    # --------------------------------------------------
    # GET /api/shop/checkout
    # Returns current cart + pre-filled addresses
    # --------------------------------------------------
    # -*- coding: utf-8 -*-
    from odoo import http
    from odoo.http import request, Response
    import json
    import logging

    _logger = logging.getLogger(__name__)

    class ShopCheckoutAPI(http.Controller):
        """
        REST API for One-Step Checkout in Odoo Website Sale.
        Supports GET to fetch cart and addresses,
        and POST to submit checkout (optional extension).
        """

        @http.route(
            '/api/shop/checkout',
            type='http',
            auth='public',
            methods=['GET', 'POST'],
            csrf=False,
            cors='*'
        )
        def get_checkout(self, **kw):
            """
            GET: Returns current sale order (cart) and prefilled billing/shipping info
            along with available countries and delivery carriers.
            """
            try:
                # Get current website order or create a new one
                website = get_current_website()
                order = website.sale_get_order(force_create=True) if website else None

                if not order:
                    return Response(
                        json.dumps({"error": "No active cart found."}),
                        status=404,
                        headers={'Content-Type': 'application/json'}
                    )

                # Prepare partner addresses
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
                        "country_id": partner.country_id.id if partner.country_id else False,
                        "country_code": partner.country_id.code if partner.country_id else "",
                        "state_id": partner.state_id.id if partner.state_id else False,
                        "state_name": partner.state_id.name if partner.state_id else "",
                    }

                # Build response
                response_data = {
                    "cart": {
                        "order_id": order.id,
                        "amount_total": order.amount_total,
                        "amount_tax": order.amount_tax,
                        "amount_untaxed": order.amount_untaxed,
                        "currency": order.currency_id.name if order.currency_id else "",
                        "lines": []
                    },
                    "billing_address": partner_to_dict(billing),
                    "shipping_address": partner_to_dict(shipping),
                    "shipping_same_as_billing": shipping.id == billing.id,
                    "available_countries": request.env['res.country'].sudo().search_read([], ['id', 'name', 'code']),
                    "available_carriers": []
                }

                # Add cart lines
                for line in order.order_line:
                    response_data["cart"]["lines"].append({
                        "id": line.id,
                        "product_id": line.product_id.id,
                        "name": line.name,
                        "quantity": line.product_uom_qty,
                        "price_unit": line.price_unit,
                        "price_subtotal": line.price_subtotal,
                        "image_url": f"/web/image/product.product/{line.product_id.id}/image_1920" if line.product_id.image_1920 else None,
                    })

                # Add available carriers
                try:
                    carriers = request.env['delivery.carrier'].sudo().available_carriers(order)
                    for carrier in carriers:
                        rate = carrier.rate_shipment(order)
                        if not rate.get('success', False):
                            _logger.warning(
                                "Carrier %s skipped due to error: %s", carrier.name, rate.get('error_message')
                            )
                            continue
                        response_data["available_carriers"].append({
                            "id": carrier.id,
                            "name": carrier.name,
                            "price": rate.get('price', 0),
                            "free_if": carrier.free_over and order.amount_untaxed >= carrier.amount,
                        })
                except Exception as e:
                    _logger.error("Failed to compute available carriers: %s", e)

                return Response(
                    json.dumps(response_data),
                    headers={'Content-Type': 'application/json'}
                )

            except Exception as e:
                _logger.exception("Error fetching checkout data: %s", e)
                return Response(
                    json.dumps({"error": "Internal server error"}),
                    status=500,
                    headers={'Content-Type': 'application/json'}
                )

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
        shipping = data.get('shipping', billing)
        same = data.get('same_as_billing', True)

        # Validate required fields for billing
        for field in required_fields:
            if not billing.get(field):
                return {"success": False, "error": f"Billing {field} is required"}

        # Validate required fields for shipping if different
        if not same:
            for field in required_fields:
                if not shipping.get(field):
                    return {"success": False, "error": f"Shipping {field} is required"}

        # Create or update billing partner
        billing_partner = self._create_or_update(billing)

        # Shipping
        if same:
            shipping_partner = billing_partner
        else:
            shipping_partner = self._create_or_update(shipping)

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
        try:
            order.with_context(from_api=True).action_confirm()
        except Exception as e:
            return {"success": False, "error": str(e)}

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
    def _create_or_update(self, values):
        Partner = request.env['res.partner'].sudo()
        values = values.copy()  # Avoid modifying original
        partner_id = values.pop('id', None)
        if partner_id:
            partner = Partner.browse(partner_id)
            if partner.exists():
                partner.write(values)
                return partner
        return Partner.create(values)
# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request, Response
import json
import logging

_logger = logging.getLogger(__name__)


def get_current_website():
    """
    Returns the current website for the request.
    Fallbacks to the first website if none is active.
    """
    website = getattr(request, 'website', False)
    if website:
        return website
    # fallback to first website in DB
    Website = request.env['website'].sudo()
    return Website.search([], limit=1)


class ApiShopCheckout(http.Controller):
    """
    REST API for One-Step Checkout in Odoo Website Sale.
    Supports GET to fetch cart and addresses,
    and POST to submit checkout (optional extension).
    """

    # --------------------------------------------------
    # GET/POST /api/shop/checkout
    # GET: Returns current cart + pre-filled addresses
    # POST: Updates cart with addresses and carrier
    # --------------------------------------------------
    @http.route('/api/shop/checkout',
                type='http', auth='public', methods=['GET', 'POST'],
                csrf=False, cors='*')
    def api_checkout(self, **kw):
        """
        Handle both GET and POST requests for checkout.
        GET: Returns current cart and addresses
        POST: Updates addresses, carrier, and returns updated cart
        """
        if request.httprequest.method == 'GET':
            return self._get_checkout_data()
        elif request.httprequest.method == 'POST':
            return self._update_checkout_data()
        else:
            return Response(
                json.dumps({"error": "Method not allowed"}),
                status=405,
                headers={'Content-Type': 'application/json'}
            )

    def _get_checkout_data(self):
        """
        GET: Returns current sale order (cart) and prefilled billing/shipping info
        along with available countries and delivery carriers.
        """
        try:
            # Get current website
            website = get_current_website()
            if not website:
                return Response(
                    json.dumps({"error": "No website found."}),
                    status=404,
                    headers={'Content-Type': 'application/json'}
                )

            # Get current order
            order = website.sale_get_order(force_create=True)

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
                    "country_name": partner.country_id.name if partner.country_id else "",
                    "state_id": partner.state_id.id if partner.state_id else False,
                    "state_name": partner.state_id.name if partner.state_id else "",
                }

            # Build response
            response_data = {
                "cart": {
                    "order_id": order.id,
                    "name": order.name,
                    "amount_total": order.amount_total,
                    "amount_tax": order.amount_tax,
                    "amount_untaxed": order.amount_untaxed,
                    "currency": {
                        "name": order.currency_id.name if order.currency_id else "",
                        "symbol": order.currency_id.symbol if order.currency_id else "",
                    },
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
                product = line.product_id
                response_data["cart"]["lines"].append({
                    "id": line.id,
                    "product_id": product.id,
                    "name": line.name,
                    "product_name": product.name,
                    "quantity": line.product_uom_qty,
                    "price_unit": line.price_unit,
                    "price_subtotal": line.price_subtotal,
                    "image_url": f"/web/image/product.product/{product.id}/image_1920" if product.image_1920 else None,
                    "product_template_id": product.product_tmpl_id.id,
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
                        "currency": order.currency_id.name,
                        "free_over": carrier.free_over,
                        "free_over_amount": carrier.amount if carrier.free_over else 0,
                        "is_free": carrier.free_over and order.amount_untaxed >= carrier.amount,
                    })
            except Exception as e:
                _logger.error("Failed to compute available carriers: %s", e)

            return Response(
                json.dumps(response_data, default=str),
                headers={'Content-Type': 'application/json'}
            )

        except Exception as e:
            _logger.exception("Error fetching checkout data: %s", e)
            return Response(
                json.dumps({"error": "Internal server error"}),
                status=500,
                headers={'Content-Type': 'application/json'}
            )

    def _update_checkout_data(self):
        """
        POST: Update checkout data (addresses, carrier, etc.)
        """
        try:
            # Get POST data
            data = request.httprequest.get_data(as_text=True)
            if not data:
                return Response(
                    json.dumps({"error": "No data provided"}),
                    status=400,
                    headers={'Content-Type': 'application/json'}
                )

            data = json.loads(data)

            # Get current website and order
            website = get_current_website()
            if not website:
                return Response(
                    json.dumps({"error": "No website found."}),
                    status=404,
                    headers={'Content-Type': 'application/json'}
                )

            order = website.sale_get_order(force_create=True)
            if not order:
                return Response(
                    json.dumps({"error": "No active cart found."}),
                    status=404,
                    headers={'Content-Type': 'application/json'}
                )

            # Update addresses if provided
            if 'billing' in data:
                billing = data.get('billing', {})
                shipping = data.get('shipping', billing)
                same = data.get('same_as_billing', True)

                required_fields = ['name', 'street', 'city', 'country_id']

                # Validate required fields for billing
                for field in required_fields:
                    if not billing.get(field):
                        return Response(
                            json.dumps({"success": False, "error": f"Billing {field} is required"}),
                            status=400,
                            headers={'Content-Type': 'application/json'}
                        )

                # Validate required fields for shipping if different
                if not same:
                    for field in required_fields:
                        if not shipping.get(field):
                            return Response(
                                json.dumps({"success": False, "error": f"Shipping {field} is required"}),
                                status=400,
                                headers={'Content-Type': 'application/json'}
                            )

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

            # Update carrier if provided
            if 'carrier_id' in data:
                carrier_id = data.get('carrier_id')
                try:
                    carrier = request.env['delivery.carrier'].sudo().browse(int(carrier_id))
                    if carrier.exists():
                        order.set_delivery_line(carrier, carrier.fixed_price)
                except (ValueError, TypeError) as e:
                    _logger.warning("Invalid carrier_id: %s", e)

            # Recompute prices
            order._recompute_prices()

            # Return updated checkout data
            return self._get_checkout_data()

        except json.JSONDecodeError:
            return Response(
                json.dumps({"error": "Invalid JSON data"}),
                status=400,
                headers={'Content-Type': 'application/json'}
            )
        except Exception as e:
            _logger.exception("Error updating checkout data: %s", e)
            return Response(
                json.dumps({"error": "Internal server error"}),
                status=500,
                headers={'Content-Type': 'application/json'}
            )

    # --------------------------------------------------
    # POST /api/shop/address (kept for backward compatibility)
    # Request body: JSON with billing & shipping
    # --------------------------------------------------
    @http.route('/api/shop/address', type='json', auth='public', methods=['POST'], csrf=False, cors='*')
    def save_address(self, **kw):
        try:
            website = get_current_website()
            if not website:
                return {"success": False, "error": "No website found."}

            order = website.sale_get_order(force_create=True)
            data = request.httprequest.get_json(force=True, silent=True) or {}

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

            # Update order with new addresses
            order._recompute_prices()

            return {
                "success": True,
                "order_id": order.id,
                "billing_address_id": billing_partner.id,
                "shipping_address_id": shipping_partner.id,
            }
        except Exception as e:
            _logger.exception("Error saving address: %s", e)
            return {"success": False, "error": "Internal server error"}

    # --------------------------------------------------
    # POST /api/shop/checkout/confirm
    # Final confirmation (payment will be handled separately)
    # --------------------------------------------------
    @http.route('/api/shop/checkout/confirm', type='json', auth='public', methods=['POST'], csrf=False, cors='*')
    def confirm_checkout(self, **kw):
        try:
            website = get_current_website()
            if not website:
                return {"success": False, "error": "No website found."}

            order = website.sale_get_order()
            if not order or order.state not in ('draft', 'sent'):
                return {"success": False, "error": "No valid cart"}

            data = request.jsonrequest

            # Set carrier if provided
            carrier_id = data.get('carrier_id')
            if carrier_id:
                try:
                    carrier = request.env['delivery.carrier'].sudo().browse(int(carrier_id))
                    if carrier.exists():
                        order.set_delivery_line(carrier, carrier.fixed_price)
                except (ValueError, TypeError) as e:
                    _logger.warning("Invalid carrier_id: %s", e)

            # Validate order has a valid partner
            if not order.partner_id or order.partner_id == request.env.ref('base.public_partner'):
                return {"success": False, "error": "Please provide billing information"}

            # Confirm the sale order
            try:
                order.with_context(send_email=True, from_api=True).action_confirm()
            except Exception as e:
                _logger.exception("Error confirming order: %s", e)
                return {"success": False, "error": str(e)}

            # Return confirmation + next step
            return {
                "success": True,
                "order_id": order.id,
                "order_name": order.name,
                "amount_total": order.amount_total,
                "currency": order.currency_id.name,
                "next_step": "payment",
                "payment_url": f"/shop/payment?order_id={order.id}",
            }
        except Exception as e:
            _logger.exception("Error in checkout confirmation: %s", e)
            return {"success": False, "error": "Internal server error"}

    # Helper: create or update partner from dict
    def _create_or_update(self, values):
        Partner = request.env['res.partner'].sudo()
        values = values.copy()  # Avoid modifying original

        # Handle state_id if state_name is provided
        state_name = values.pop('state_name', None)
        country_id = values.get('country_id')

        if state_name and country_id:
            state = request.env['res.country.state'].sudo().search([
                ('country_id', '=', int(country_id)),
                ('name', '=ilike', state_name)
            ], limit=1)
            if state:
                values['state_id'] = state.id

        # Handle country_id conversion
        if 'country_id' in values:
            values['country_id'] = int(values['country_id'])

        if 'state_id' in values and values['state_id']:
            values['state_id'] = int(values['state_id'])

        partner_id = values.pop('id', None)
        if partner_id:
            partner = Partner.browse(int(partner_id))
            if partner.exists():
                partner.write(values)
                return partner

        return Partner.create(values)
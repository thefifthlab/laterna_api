# -*- coding: utf-8 -*-
from odoo import http, fields
from odoo.http import request
import logging
import json

_logger = logging.getLogger(__name__)

class CartAPI(http.Controller):

    # ── Helper Methods ────────────────────────────────────────────────────────

    def _json_error_response(self, message, code=400, error_type="error"):
        """Returns a standardized JSON error response."""
        payload = {
            'status': 'error',
            'error_type': error_type,
            'message': message
        }
        return request.make_response(
            json.dumps(payload),
            headers=[('Content-Type', 'application/json')],
            status=code
        )

    def _log_event(self, event, client_ip, **kwargs):
        """Utility for structured logging."""
        log_data = {
            'timestamp': fields.Datetime.now(),
            'event': event,
            'ip': client_ip,
            **kwargs
        }
        _logger.info(json.dumps(log_data, default=str))

    # ── Routes ────────────────────────────────────────────────────────────────

    import json
    import logging
    from odoo import http
    from odoo.http import request

    _logger = logging.getLogger(__name__)

    @http.route(
        '/api/v1/cart/add',
        type='http',
        auth="public",
        website=True,
        csrf=False,
        methods=['POST'],
        cors="*"
    )
    def add_to_cart(self, **kwargs):
        """
        Add one or multiple products to the cart.

        Accepts JSON body in two forms:

        Single product:
            {
                "product_id": 42,
                "quantity": 2
            }

        Multiple products:
            {
                "items": [
                    {"product_id": 42, "quantity": 2},
                    {"product_id": 7,  "quantity": 1}
                ]
            }
        """
        client_ip = request.httprequest.remote_addr

        try:
            # ── Parse Body ────────────────────────────────────────────────────
            content_type = request.httprequest.headers.get('Content-Type', '').lower()

            if 'application/json' in content_type:
                try:
                    data = request.get_json_data()
                except Exception:
                    return self._json_error_response(
                        "Invalid JSON body", code=400, error_type="bad_request"
                    )
            else:
                data = dict(request.params)

            # ── Normalise into a list of items ────────────────────────────────
            # Support both {"items": [...]} and {"product_id": X, "quantity": Y}
            if 'items' in data:
                raw_items = data['items']
                if not isinstance(raw_items, list) or len(raw_items) == 0:
                    return self._json_error_response(
                        "'items' must be a non-empty list", code=400, error_type="validation_error"
                    )
            elif 'product_id' in data:
                raw_items = [{'product_id': data.get('product_id'), 'quantity': data.get('quantity', 1)}]
            else:
                return self._json_error_response(
                    "Provide either 'product_id' or 'items'", code=400, error_type="validation_error"
                )

            # ── Validate & parse each item ────────────────────────────────────
            parsed_items = []
            for idx, item in enumerate(raw_items):
                label = f"items[{idx}]"

                pid_raw = item.get('product_id')
                qty_raw = item.get('quantity', 1)

                if pid_raw is None:
                    return self._json_error_response(
                        f"Missing 'product_id' in {label}", code=400, error_type="validation_error"
                    )

                try:
                    product_id = int(pid_raw)
                    quantity = float(qty_raw)
                except (ValueError, TypeError):
                    return self._json_error_response(
                        f"Invalid types in {label}: 'product_id' must be int, 'quantity' a number",
                        code=400, error_type="validation_error"
                    )

                if quantity <= 0:
                    return self._json_error_response(
                        f"'quantity' in {label} must be greater than 0",
                        code=400, error_type="validation_error"
                    )

                if quantity > 10_000:
                    return self._json_error_response(
                        f"'quantity' in {label} exceeds maximum allowed (10,000)",
                        code=400, error_type="validation_error"
                    )

                parsed_items.append({'product_id': product_id, 'quantity': quantity})

            # ── Validate all products exist and are purchasable ───────────────
            product_ids = [i['product_id'] for i in parsed_items]
            products = request.env['product.product'].sudo().browse(product_ids)

            for product, item in zip(products, parsed_items):
                if not product.exists() or not product.active or not product.sale_ok:
                    return self._json_error_response(
                        f"Product ID {item['product_id']} not found or unavailable for sale",
                        code=404, error_type="not_found"
                    )

            # ── Get or create a draft cart ────────────────────────────────────
            sale_order = request.website.sale_get_order()

            if sale_order and sale_order.state not in ('draft', 'sent'):
                _logger.info(
                    "Session cart %s already confirmed — resetting for new order.", sale_order.id
                )
                request.website.sale_reset()
                sale_order = None

            if not sale_order:
                sale_order = request.website.sale_get_order(force_create=True)

            # ── Add all items to the cart ─────────────────────────────────────
            for item in parsed_items:
                sale_order._cart_update(
                    product_id=item['product_id'],
                    add_qty=item['quantity'],
                )

            # ── Build response ────────────────────────────────────────────────
            STATE_LABELS = {
                'draft': 'Draft Quotation',
                'sent': 'Quotation Sent',
                'sale': 'Sales Order',
                'done': 'Locked',
                'cancel': 'Cancelled',
            }

            order_items = [{
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
                    'status': STATE_LABELS.get(sale_order.state, sale_order.state),
                    'order_reference': sale_order.name,
                    'currency': sale_order.currency_id.name,
                    'summary': {
                        'untaxed_amount': sale_order.amount_untaxed,
                        'tax_amount': sale_order.amount_tax,
                        'total_amount': sale_order.amount_total,
                        'items_count': int(sale_order.cart_quantity),
                    },
                    'items': order_items,
                }
            }

            self._log_event(
                'cart_add_success', client_ip,
                product_ids=product_ids, order_id=sale_order.id
            )

            return request.make_response(
                json.dumps(payload, default=str),
                headers=[('Content-Type', 'application/json')],
                status=200
            )

        except Exception as e:
            _logger.error("Cart API Fatal Error: %s", str(e), exc_info=True)
            self._log_event('cart_add_error', client_ip, error=str(e))
            return self._json_error_response(
                "Internal server error. Please try again later.", code=500
            )

    @http.route(
        '/api/v1/cart/pay',
        type='http',
        auth="public",
        website=True,
        csrf=False,
        methods=['POST'],
        cors="*"
    )
    def pay_cart(self, **kwargs):
        """
        Accepts payment for a cart order, then:
        1. Confirms the sale order
        2. Creates and posts the invoice
        3. Registers payment via Odoo journal → marks invoice as paid
           (SKIPPED when payment_method='paystack' — Paystack already reconciled it)
        """
        try:
            client_ip = request.httprequest.remote_addr
            content_type = request.httprequest.headers.get('Content-Type', '').lower()

            if 'application/json' in content_type:
                try:
                    data = request.get_json_data()
                except Exception:
                    return self._json_error_response("Invalid JSON body", code=400)
            else:
                data = request.params

            # ── Support direct kwargs calls (e.g. from _process_notification_data) ──
            # When called internally (not via HTTP), kwargs are passed directly.
            # request.params will be empty in that case, so fall back to kwargs.
            cart_id_raw = data.get('cart_id') or kwargs.get('cart_id')
            payment_method_ref = data.get('payment_method') or kwargs.get('payment_method')
            amount_paid_raw = data.get('amount_paid') or kwargs.get('amount_paid')

            # ── Required fields ───────────────────────────────────────────────────
            if not cart_id_raw:
                return self._json_error_response("Missing 'cart_id'", code=400)
            if not payment_method_ref:
                return self._json_error_response("Missing 'payment_method'", code=400)
            if not amount_paid_raw:
                return self._json_error_response("Missing 'amount_paid'", code=400)

            try:
                cart_id = int(cart_id_raw)
                amount_paid = float(amount_paid_raw)
            except (ValueError, TypeError):
                return self._json_error_response(
                    "'cart_id' must be integer, 'amount_paid' must be a number", code=400
                )

            # ── Determine if this was triggered by an online payment provider ─────
            # Provider-handled payments (e.g. Paystack) already create and reconcile
            # an account.payment via payment.transaction._set_done(). Registering a
            # second journal payment would double-count. We confirm the order and
            # post the invoice only, then skip Step 4.
            is_provider_payment = payment_method_ref in ('paystack', 'stripe', 'flutterwave')

            # ── Load the sale order ───────────────────────────────────────────────
            sale_order = request.env['sale.order'].sudo().browse(cart_id)
            if not sale_order.exists():
                return self._json_error_response(
                    f"Cart/Order ID {cart_id} not found", code=404
                )

            if sale_order.state == 'cancel':
                return self._json_error_response(
                    "This order has been cancelled", code=400
                )

            # ── Validate payment amount matches order total ───────────────────────
            if round(amount_paid, 2) < round(sale_order.amount_total, 2):
                return self._json_error_response(
                    f"Amount paid ({amount_paid}) is less than order total "
                    f"({sale_order.amount_total})",
                    code=400
                )

            # ── Step 1: Confirm the order ─────────────────────────────────────────
            if sale_order.state in ['draft', 'sent']:
                sale_order.action_confirm()

            # ── Step 2: Create & post invoice ─────────────────────────────────────
            existing_invoices = sale_order.invoice_ids.filtered(
                lambda inv: inv.state != 'cancel'
            )
            if existing_invoices:
                invoice = existing_invoices[0]
            else:
                invoice = sale_order._create_invoices()

            if invoice.state == 'draft':
                invoice.action_post()

            # ── Step 3 & 4: Register journal payment ─────────────────────────────
            # Skip entirely for provider payments — Paystack's _set_done() already
            # created an account.payment and reconciled it against this invoice.
            # Running account.payment.register again would create a duplicate entry.
            if is_provider_payment:
                _logger.info(
                    "pay_cart: skipping journal payment registration for order %s "
                    "— payment handled by provider '%s'",
                    sale_order.name, payment_method_ref
                )
            else:
                # Cash / bank flow: find the correct journal and register payment
                company = sale_order.company_id

                journal_type = 'cash' if payment_method_ref == 'cash' else 'bank'

                payment_method_line = request.env['account.payment.method.line'].sudo().search([
                    ('payment_type', '=', 'inbound'),
                    ('journal_id.type', '=', journal_type),
                    ('journal_id.company_id', '=', company.id),
                ], limit=1)

                if not payment_method_line:
                    return self._json_error_response(
                        f"No inbound '{journal_type}' payment method found for company "
                        f"'{company.name}'. Check your accounting journals.",
                        code=500
                    )

                if invoice.state == 'posted' and invoice.payment_state not in ('paid', 'in_payment'):
                    payment_register = request.env['account.payment.register'].sudo().with_company(
                        company
                    ).with_context(
                        active_model='account.move',
                        active_ids=invoice.ids,
                    ).create({
                        'payment_date': fields.Date.today(),
                        'journal_id': payment_method_line.journal_id.id,
                        'payment_method_line_id': payment_method_line.id,
                        'amount': invoice.amount_residual,
                    })
                    payment_register.action_create_payments()

            # Refresh invoice after any payment activity
            invoice.invalidate_recordset()

            # ── Build response ────────────────────────────────────────────────────
            STATE_LABELS = {
                'draft': 'Draft Quotation',
                'sent': 'Quotation Sent',
                'sale': 'Sales Order',
                'done': 'Locked',
                'cancel': 'Cancelled',
            }

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
                    'status': STATE_LABELS.get(sale_order.state, sale_order.state),
                    'order_reference': sale_order.name,
                    'currency': sale_order.currency_id.name,
                    'summary': {
                        'untaxed_amount': sale_order.amount_untaxed,
                        'tax_amount': sale_order.amount_tax,
                        'total_amount': sale_order.amount_total,
                        'items_count': int(sale_order.cart_quantity),
                    },
                    'items': items,
                    'invoice': {
                        'invoice_id': invoice.id,
                        'invoice_reference': invoice.name,
                        'invoice_state': invoice.state,
                        'payment_state': invoice.payment_state,
                        'amount_due': invoice.amount_residual,
                    }
                }
            }

            self._log_event('cart_pay_success', client_ip,
                            cart_id=cart_id, invoice_id=invoice.id)

            return request.make_response(
                json.dumps(payload, default=str),
                headers=[('Content-Type', 'application/json')],
                status=200
            )

        except Exception as e:
            _logger.error(f"Cart Pay API Fatal Error: {str(e)}", exc_info=True)
            return self._json_error_response(
                "Internal server error. Contact support.", code=500
            )


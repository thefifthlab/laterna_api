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
        type='http',
        auth="public",
        website=True,
        csrf=False,
        methods=['POST'],
        cors="*"
    )
    def add_to_cart(self, **kwargs):
        """
        Adds a product to the cart.
        Always creates a new draft cart if the existing one is already confirmed.
        """
        try:
            client_ip = request.httprequest.remote_addr
            content_type = request.httprequest.headers.get('Content-Type', '').lower()

            if 'application/json' in content_type:
                try:
                    data = request.get_json_data()
                except Exception:
                    return self._json_error_response(
                        "Invalid JSON body", code=400, error_type="bad_request"
                    )
            else:
                data = request.params

            # ── Extract fields ────────────────────────────────────────────────
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

            # ── Product check ─────────────────────────────────────────────────
            product = request.env['product.product'].sudo().browse(product_id)
            if not product.exists() or not product.active or not product.sale_ok:
                return self._json_error_response(
                    f"Product ID {product_id} not found or not available", code=404
                )

            # ── Cart logic ────────────────────────────────────────────────────
            sale_order = request.website.sale_get_order()

            # If existing order is already confirmed/locked, drop it and start fresh
            if sale_order and sale_order.state not in ['draft', 'sent']:
                request.session.pop('sale_order_id', None)
                request.session.pop('website_sale_cart_quantity', None)
                sale_order = None

            if not sale_order:
                sale_order = request.website.sale_get_order(force_create=True)

            sale_order._cart_update(
                product_id=product.id,
                add_qty=quantity
            )

            # ── Build response ────────────────────────────────────────────────
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
                }
            }

            self._log_event('cart_add_success', client_ip,
                            product_id=product_id, qty=quantity, order_id=sale_order.id)

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
        3. Registers payment → marks invoice as paid
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

            # ── Required fields ───────────────────────────────────────────────
            cart_id_raw = data.get('cart_id')
            payment_method_ref = data.get('payment_method')  # "cash" or "bank"
            amount_paid_raw = data.get('amount_paid')  # must match order total

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

            # ── Load the sale order ───────────────────────────────────────────
            sale_order = request.env['sale.order'].sudo().browse(cart_id)
            if not sale_order.exists():
                return self._json_error_response(
                    f"Cart/Order ID {cart_id} not found", code=404
                )

            if sale_order.state == 'cancel':
                return self._json_error_response(
                    "This order has been cancelled", code=400
                )

            # ── Validate payment amount matches order total ───────────────────
            if round(amount_paid, 2) < round(sale_order.amount_total, 2):
                return self._json_error_response(
                    f"Amount paid ({amount_paid}) is less than order total "
                    f"({sale_order.amount_total})",
                    code=400
                )

            # ── Step 1: Confirm the order ─────────────────────────────────────
            if sale_order.state in ['draft', 'sent']:
                sale_order.action_confirm()

            # ── Step 2: Create & post invoice ─────────────────────────────────
            existing_invoices = sale_order.invoice_ids.filtered(
                lambda inv: inv.state != 'cancel'
            )
            if existing_invoices:
                invoice = existing_invoices[0]
            else:
                invoice = sale_order._create_invoices()

            if invoice.state == 'draft':
                invoice.action_post()  # Validate/confirm the invoice

            # ── Step 3: Find payment journal (scoped to order's company) ──────
            company = sale_order.company_id  # get the order's company

            journal_type = 'cash' if payment_method_ref == 'cash' else 'bank'

            payment_method_line = request.env['account.payment.method.line'].sudo().search([
                ('payment_type', '=', 'inbound'),
                ('journal_id.type', '=', journal_type),
                ('journal_id.company_id', '=', company.id),  # scope to same company
            ], limit=1)

            if not payment_method_line:
                return self._json_error_response(
                    f"No inbound '{journal_type}' payment method found for company "
                    f"'{company.name}'. Check your accounting journals.",
                    code=500
                )

            # ── Step 4: Register payment (force correct company context) ───────
            if invoice.state == 'posted' and invoice.payment_state != 'paid':
                payment_register = request.env['account.payment.register'].sudo().with_company(
                    company  # force company context
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

            # Refresh invoice after payment
            invoice.invalidate_recordset()

            # ── Build response ────────────────────────────────────────────────
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
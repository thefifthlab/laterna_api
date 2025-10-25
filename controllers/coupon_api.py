from odoo import http
from odoo.http import request
import json


class coupon_api(http.Controller):
    @http.route('/api/coupon/create', type='http', auth='public', methods=['POST'], csrf=False)
    def create_coupon(self, **kwargs):
        try:
            # Validate required input
            program_id = kwargs.get('program_id')
            if not program_id or not program_id.isdigit():
                return json.dumps({
                    'status': 'error',
                    'message': 'Invalid or missing program_id'
                })

            # Optional: partner_id for targeted coupon
            partner_id = kwargs.get('partner_id', False)
            if partner_id and not partner_id.isdigit():
                return json.dumps({
                    'status': 'error',
                    'message': 'Invalid partner_id'
                })

            # Check if program exists
            program = request.env['sale.coupon.program'].sudo().browse(int(program_id))
            if not program.exists():
                return json.dumps({
                    'status': 'error',
                    'message': 'Loyalty program not found'
                })

            # Prepare coupon data
            coupon_data = {
                'program_id': int(program_id),
                'partner_id': int(partner_id) if partner_id else False,
            }

            # Create coupon
            coupon = request.env['sale.coupon'].sudo().create(coupon_data)
            if not coupon:
                return json.dumps({
                    'status': 'error',
                    'message': 'Failed to create coupon'
                })

            # Return coupon details
            return json.dumps({
                'status': 'success',
                'coupon': {
                    'id': coupon.id,
                    'code': coupon.code,
                    'program_name': program.name,
                    'expiration_date': coupon.expiration_date.strftime('%Y-%m-%d') if coupon.expiration_date else False,
                    'state': coupon.state,
                }
            })

        except Exception as e:
            return json.dumps({
                'status': 'error',
                'message': str(e)
            })
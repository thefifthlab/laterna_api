from odoo import http, fields
from odoo.http import request
from odoo.exceptions import ValidationError
import json
import logging
import re

_logger = logging.getLogger(__name__)


class GuestUser(http.Controller):
    @http.route('/api/v1/guest/user/create', type='json', auth='public', methods=['POST'], csrf=False, cors='*')
    def create_portal_user(self, **kwargs):
        """API to create a portal user for e-commerce."""
        try:
            # Parse JSON data
            try:
                data = json.loads(request.httprequest.data)
            except json.JSONDecodeError:
                return self._make_response(
                    {'error': 'Invalid JSON data'},
                    status=400
                )

            # Validate required fields
            required_fields = ['email', 'password', 'name']
            if not all(data.get(field) for field in required_fields):
                return self._make_response(
                    {'error': 'Missing required fields: email, password, and name are required'},
                    status=400
                )

            email = data.get('email')
            # Validate email format
            if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
                return self._make_response(
                    {'error': 'Invalid email format'},
                    status=400
                )

            # Validate password strength
            password = data.get('password')
            if not re.match(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$", password):
                return self._make_response(
                    {
                        'error': 'Password must be at least 8 characters with uppercase, lowercase, number, and special character'},
                    status=400
                )

            # Validate phone number (optional)
            phone = data.get('phone', False)
            if phone and not re.match(r"^\+?\d{10,15}$", phone):
                return self._make_response(
                    {'error': 'Invalid phone number format'},
                    status=400
                )

            # Check if user already exists
            existing_user = request.env['res.users'].sudo().search([('login', '=', email)], limit=1)
            if existing_user:
                return self._make_response(
                    {'error': 'Email already exists'},
                    status=400
                )

            # Create partner (e-commerce customer)
            with request.env.cr.savepoint():
                partner = request.env['res.partner'].sudo().create({
                    'name': data.get('name'),
                    'email': email,
                    'phone': phone,
                    'company_type': 'person',
                    'customer_rank': 1,  # Mark as customer for e-commerce
                    'street': data.get('street', False),
                    'city': data.get('city', False),
                    'zip': data.get('zip', False),
                    'country_id': request.env['res.country'].search(
                        [('code', '=', data.get('country_code', False))], limit=1).id
                    if data.get('country_code') else False,
                })

                # Create portal user
                portal_group = request.env.ref('base.group_portal', raise_if_not_found=False)
                if not portal_group:
                    return self._make_response(
                        {'error': 'Portal group not found'},
                        status=500
                    )

                user = request.env['res.users'].sudo().with_context(no_reset_password=True).create({
                    'name': data.get('name'),
                    'login': email,
                    'password': password,
                    'partner_id': partner.id,
                    'groups_id': [(6, 0, [portal_group.id])],
                })

            # Send welcome email
            try:
                template = request.env.ref('auth_signup.mail_template_user_signup_account_created',
                                           raise_if_not_found=False)
                if template:
                    template.sudo().with_context(lang=user.lang).send_mail(
                        user.id,
                        email_values={'email_to': email, 'email_from': request.env.company.email}
                    )
                else:
                    _logger.warning(
                        "Welcome email template 'auth_signup.mail_template_user_signup_account_created' not found")
            except Exception as e:
                _logger.warning("Failed to send welcome email for %s: %s", email, str(e))

            return self._make_response({
                'user_id': user.id,
                'partner_id': partner.id,
                'name': user.name,
                'message': 'Portal user created successfully',
                'created_at': fields.Datetime.now().isoformat()
            }, status=201)

        except ValidationError as ve:
            _logger.error("Validation error for email %s: %s", email, str(ve))
            return self._make_response(
                {'error': f'Validation error: {str(ve)}'},
                status=400
            )
        except Exception as e:
            _logger.error("Error creating portal user for email %s: %s", email, str(e))
            return self._make_response(
                {'error': 'Internal server error'},
                status=500
            )

    def _make_response(self, data, status=200, headers=None):
        """Helper method to create consistent JSON responses."""
        headers = headers or []
        headers.append(('Content-Type', 'application/json'))
        headers.append(('Access-Control-Allow-Origin', '*'))  # Adjust CORS as needed
        return request.make_response(
            json.dumps(data),
            headers=headers,
            status=status
        )

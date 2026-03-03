from odoo import http
from odoo.http import request
import json
import re
import logging

_logger = logging.getLogger(__name__)


class SuperAdminApiController(http.Controller):
    @http.route(['/api/v1/create_super_admin'], type='json', auth='public', csrf=False, methods=['POST'], cors='*')
    def create_super_admin(self, **kwargs):
        """
        Create a super admin user with full validation.
        Request payload example:
        {
            "name": "Gbolahan Folarin",
            "email": "folaringbolahan@gmail.com",
            "password": "Admin@12345",
            "phone": "09037564449",
            "street": "Mutiu Ojomu Close, Lakowe, Phase II",
            "city": "Lakowe",
            "state": "Lagos",
            "country": "Nigeria"
        }
        """
        ip_address = request.httprequest.environ.get(
            'HTTP_X_FORWARDED_FOR',
            request.httprequest.environ.get('REMOTE_ADDR')
        )

        # ------------------------
        # 1. Extract payload
        # ------------------------
        data = None
        try:
            if hasattr(request, 'jsonrequest') and request.jsonrequest:
                data = request.jsonrequest
            else:
                raw_data = request.httprequest.get_data().decode('utf-8')
                if raw_data:
                    data = json.loads(raw_data)
                else:
                    data = kwargs
        except Exception as e:
            _logger.error("JSON parse error: %s", str(e))
            return {'success': False, 'error': 'Invalid JSON format'}

        if not data:
            return {'success': False, 'error': 'No data received'}

        # ------------------------
        # 2. Validate required fields
        # ------------------------
        required_fields = ['name', 'email', 'password', 'street', 'city', 'state', 'country']
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            return {'success': False, 'error': f"Missing required fields: {', '.join(missing)}"}

        name = data['name'].strip()
        email = data['email'].strip()
        password = data['password']
        street = data['street'].strip()
        city = data['city'].strip()
        state_name = data['state'].strip()
        country_name = data['country'].strip()
        phone = data.get('phone')

        # ------------------------
        # 3. Validate email
        # ------------------------
        if not self._is_valid_email(email):
            return {'success': False, 'error': 'Invalid email format'}

        # ------------------------
        # 4. Validate password
        # ------------------------
        password_errors = self._validate_password_strength(password)
        if password_errors:
            return {'success': False, 'error': 'Password does not meet requirements', 'details': password_errors}

        # ------------------------
        # 5. Validate name
        # ------------------------
        if not self._is_valid_name(name):
            return {'success': False, 'error': 'Name contains invalid characters'}

        # ------------------------
        # 6. Validate country and state
        # ------------------------
        country = request.env['res.country'].sudo().search([('name', 'ilike', country_name)], limit=1)
        if not country:
            return {'success': False, 'error': 'Invalid country name'}

        state = request.env['res.country.state'].sudo().search(
            [('name', 'ilike', state_name), ('country_id', '=', country.id)], limit=1
        )
        if not state:
            return {'success': False, 'error': 'Invalid state name or not in the specified country'}

        # ------------------------
        # 7. Check if user already exists
        # ------------------------
        existing_user = request.env['res.users'].sudo().search([('login', '=', email)], limit=1)
        if existing_user:
            return {'success': False, 'error': 'Email already exists'}

        # ------------------------
        # 8. Create partner and super admin user safely
        # ------------------------
        try:
            Partner = request.env['res.partner'].sudo()
            User = request.env['res.users'].sudo()

            # Partner creation
            partner_vals = {
                'name': name,
                'email': email,
                'street': street,
                'city': city,
                'state_id': state.id,
                'country_id': country.id,
            }
            if phone:
                partner_vals['phone'] = phone

            partner = Partner.create(partner_vals)
            request.env.cr.commit()  # commit to avoid transaction issues

            # User creation with admin & internal groups
            group_system = request.env.ref("base.group_system")
            group_internal = request.env.ref("base.group_user")

            user_vals = {
                'name': name,
                'login': email,
                'password': password,
                'partner_id': partner.id,
                'groups_id': [(6, 0, [group_system.id, group_internal.id])]
            }

            user = User.with_context(no_reset_password=True).create(user_vals)
            request.env.cr.commit()

            return {
                'success': True,
                'data': {
                    'user_id': user.id,
                    'partner_id': partner.id,
                    'message': 'Super admin account created successfully'
                }
            }

        except Exception as e:
            request.env.cr.rollback()
            _logger.error("Failed to create super admin for %s: %s", email, str(e), exc_info=True)
            return {'success': False, 'error': 'Internal server error'}

    # ------------------------
    # Helper Methods
    # ------------------------
    def _is_valid_email(self, email):
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email)) and len(email) <= 254

    def _validate_password_strength(self, password):
        errors = []
        if len(password) < 8:
            errors.append("Password must be at least 8 characters")
        if not any(c.isupper() for c in password):
            errors.append("Password must contain an uppercase letter")
        if not any(c.islower() for c in password):
            errors.append("Password must contain a lowercase letter")
        if not any(c.isdigit() for c in password):
            errors.append("Password must contain a digit")
        if not any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?`~' for c in password):
            errors.append("Password must contain a special character")
        if re.search(r'(.)\1{2,}', password):
            errors.append("Avoid repeated characters")
        return errors

    def _is_valid_name(self, name):
        return bool(re.match(r"^[a-zA-Z\s\-'\.]+$", name.strip())) if name else False

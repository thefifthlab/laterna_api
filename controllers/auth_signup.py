# -*- coding: utf-8 -*-
import json
import logging
import re
from odoo import http, fields
from odoo.http import request
from odoo.exceptions import ValidationError, AccessError
from werkzeug.wrappers import Response
import json

_logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Regexes
# ----------------------------------------------------------------------
EMAIL_REGEX = re.compile(
    r"^(?=.{1,254}$)(?=.{1,64}@)[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)
PASSWORD_REGEX = re.compile(
    r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$"
)


class InternalUserRegistration(http.Controller):
    """
    Public API to register internal users (employees/staff)
    """

    @http.route("/api/v1/admin/auth/register",
                type="http",
                auth="public",
                methods=["POST"],
                csrf=False,
                cors="*")
    def internal_auth_signup(self, **kwargs):
        """
        Register a new internal user (employee)

        Required fields: email, password, name
        Optional: phone, department, job_position, employee_id
        """
        try:
            # 1. Check content type
            if request.httprequest.mimetype != 'application/json':
                return self._json_error("Content-Type must be application/json", 415)

            # 2. Parse JSON data
            try:
                data = request.get_json_data()
            except Exception as e:
                return self._json_error(f"Invalid JSON: {str(e)}", 400)

            # 3. Validate required fields
            required_fields = ["email", "password", "name"]
            missing_fields = [field for field in required_fields if not data.get(field)]

            if missing_fields:
                return self._json_error(
                    f"Missing required fields: {', '.join(missing_fields)}",
                    400
                )

            email = data["email"].strip().lower()
            password = data["password"]
            name = data["name"].strip()

            # 4. Validate email format
            if not EMAIL_REGEX.match(email):
                return self._json_error("Invalid email format", 400)

            # 5. Validate password strength
            if not PASSWORD_REGEX.match(password):
                return self._json_error(
                    "Password must contain at least 8 characters including: "
                    "uppercase letter, lowercase letter, digit, and special character (@$!%*?&)",
                    400
                )

            # 6. Check if user already exists
            existing_user = request.env["res.users"].sudo().search(
                [("login", "=", email)],
                limit=1
            )
            if existing_user:
                return self._json_error("Email already registered", 409)

            # 7. Process phone number
            phone = data.get("phone", "").strip()
            if phone:
                # Clean phone number
                if phone.startswith('+'):
                    phone = '+' + ''.join(filter(str.isdigit, phone[1:]))
                else:
                    phone = ''.join(filter(str.isdigit, phone))

                # Validation
                digits_only = phone.lstrip('+')
                if len(digits_only) < 8 or len(digits_only) > 15:
                    return self._json_error("Phone number must be 8-15 digits", 400)

            # 8. Get internal user group
            # For Odoo 18, internal users typically get these groups:
            # - base.group_user (Employee)
            # - base.group_erp_manager (optional, for ERP access)
            # - base.group_system (optional, for admin access)

            employee_group = request.env.ref("base.group_user")
            if not employee_group:
                return self._json_error("System configuration error: Employee group not found", 500)

            # Optional: Additional groups based on role/request
            additional_group_ids = []

            # Check if user should have specific access
            if data.get("is_manager"):
                manager_group = request.env.ref("base.group_erp_manager")
                if manager_group:
                    additional_group_ids.append(manager_group.id)

            if data.get("is_admin"):
                admin_group = request.env.ref("base.group_system")
                if admin_group:
                    additional_group_ids.append(admin_group.id)

            # Combine groups
            group_ids = [employee_group.id] + additional_group_ids

            # 9. Create user in transaction
            with request.env.cr.savepoint():
                # Create partner
                partner_vals = {
                    "name": name,
                    "email": email,
                    "phone": phone or False,
                    "company_type": "person",
                    "customer_rank": 0,  # Internal users are not customers by default
                    "street": data.get("street", "").strip(),
                    "city": data.get("city", "").strip(),
                    "zip": data.get("zip", "").strip(),
                }

                # Handle country
                country_code = data.get("country_code", "").strip().upper()
                if country_code:
                    country = request.env["res.country"].sudo().search(
                        [("code", "=", country_code)],
                        limit=1
                    )
                    if country:
                        partner_vals["country_id"] = country.id

                partner = request.env["res.partner"].sudo().create(partner_vals)

                # Create internal user
                user_vals = {
                    "name": name,
                    "login": email,
                    "password": password,
                    "partner_id": partner.id,
                    "groups_id": [(6, 0, group_ids)],
                    "active": True,
                    # Internal user specific fields
                    "notification_type": "email",  # Default notification preference
                }

                user = request.env["res.users"].sudo().with_context(
                    no_reset_password=True,
                    mail_create_nosubscribe=True,
                    mail_create_nolog=True
                ).create(user_vals)

                # 10. Create employee record (optional but recommended)
                try:
                    employee_vals = {
                        "name": name,
                        "work_email": email,
                        "user_id": user.id,
                        "address_home_id": partner.id,
                        "mobile_phone": phone or False,
                        "department_id": self._get_department_id(data.get("department")),
                        "job_id": self._get_job_position_id(data.get("job_position")),
                        "identification_id": data.get("employee_id", "").strip(),
                    }

                    employee = request.env["hr.employee"].sudo().create(employee_vals)

                    # Link user to employee
                    user.employee_id = employee.id

                except Exception as emp_error:
                    _logger.warning(f"Could not create employee record: {str(emp_error)}")
                    # Continue even if employee creation fails

                # 11. Send welcome email (optional)
                send_welcome_email = data.get("send_welcome_email", True)
                if send_welcome_email:
                    try:
                        template = request.env.ref("auth_signup.mail_template_user_signup_account_created")
                        if template:
                            template.sudo().with_context(
                                lang=user.lang,
                                login=email,
                                password=password,
                                name=name,
                                object=user,
                                tpl_force_email_to=email
                            ).send_mail(user.id, force_send=True)
                    except Exception as email_error:
                        _logger.warning(f"Could not send welcome email: {str(email_error)}")

                # 12. Log the registration
                _logger.info(f"Internal user registered: {email} (ID: {user.id})")

            # 13. Success response
            response_data = {
                "success": True,
                "message": "Internal user registered successfully",
                "data": {
                    "user_id": user.id,
                    "partner_id": partner.id,
                    "employee_id": getattr(user, 'employee_id', {}).get('id'),
                    "name": user.name,
                    "email": user.email,
                    "login": user.login,
                    "is_internal": True,
                    "groups": [g.name for g in user.groups_id],
                },
                "timestamp": fields.Datetime.now().isoformat(),
            }

            return request.make_json_response(response_data, status=201)

        except ValidationError as e:
            _logger.warning(f"Validation error: {str(e)}")
            return self._json_error(f"Validation error: {str(e)}", 400)
        except AccessError as e:
            _logger.error(f"Access error: {str(e)}")
            return self._json_error("Permission denied", 403)
        except Exception as e:
            _logger.exception(f"Unexpected error: {str(e)}")
            return self._json_error("Internal server error", 500)

    def _get_department_id(self, department_name):
        """Get or create department"""
        if not department_name:
            return False

        department = request.env["hr.department"].sudo().search(
            [("name", "=ilike", department_name.strip())],
            limit=1
        )

        if not department:
            # Create new department if it doesn't exist
            department = request.env["hr.department"].sudo().create({
                "name": department_name.strip(),
                "company_id": request.env.company.id,
            })

        return department.id

    def _get_job_position_id(self, job_title):
        """Get or create job position"""
        if not job_title:
            return False

        job = request.env["hr.job"].sudo().search(
            [("name", "=ilike", job_title.strip())],
            limit=1
        )

        if not job:
            # Create new job position
            job = request.env["hr.job"].sudo().create({
                "name": job_title.strip(),
                "company_id": request.env.company.id,
            })

        return job.id

    def _json_error(self, error_msg, status=400):
        """Return JSON error response"""
        return request.make_json_response({
            "success": False,
            "error": error_msg,
            "status": status
        }, status=status)


# Alternative: Simple version for basic internal user creation
class SimpleInternalRegistration(http.Controller):

    @http.route("/api/v1/internal/register/simple",
                type="http",
                auth="public",
                methods=["POST"],
                csrf=False,
                cors="*")
    def register_internal_simple(self, **kwargs):
        """Simple version for internal user registration"""

        try:
            # Get request data
            request_data = request.httprequest.get_data().decode('utf-8')
            data = json.loads(request_data) if request_data else {}

            # Basic validation
            if not all(k in data for k in ['email', 'password', 'name']):
                return Response(
                    json.dumps({
                        "success": False,
                        "error": "Missing required fields: email, password, name"
                    }),
                    status=400,
                    content_type='application/json'
                )

            email = data['email'].strip().lower()
            name = data['name'].strip()

            # Check duplicate
            if request.env["res.users"].sudo().search([("login", "=", email)], limit=1):
                return Response(
                    json.dumps({
                        "success": False,
                        "error": "Email already registered"
                    }),
                    status=409,
                    content_type='application/json'
                )

            # Create internal user
            partner = request.env["res.partner"].sudo().create({
                "name": name,
                "email": email,
                "company_type": "person",
            })

            # Get employee group
            employee_group = request.env.ref("base.group_user")

            user = request.env["res.users"].sudo().with_context(
                no_reset_password=True
            ).create({
                "name": name,
                "login": email,
                "password": data['password'],
                "partner_id": partner.id,
                "groups_id": [(6, 0, [employee_group.id])],
                "active": True,
            })

            # Optional: Create employee record
            try:
                request.env["hr.employee"].sudo().create({
                    "name": name,
                    "work_email": email,
                    "user_id": user.id,
                    "address_home_id": partner.id,
                })
            except:
                pass  # Skip if HR module not installed or fails

            return Response(
                json.dumps({
                    "success": True,
                    "message": "Internal user created successfully",
                    "user_id": user.id,
                    "login": user.login,
                }),
                status=201,
                content_type='application/json'
            )

        except json.JSONDecodeError:
            return Response(
                json.dumps({
                    "success": False,
                    "error": "Invalid JSON"
                }),
                status=400,
                content_type='application/json'
            )
        except Exception as e:
            return Response(
                json.dumps({
                    "success": False,
                    "error": str(e)
                }),
                status=500,
                content_type='application/json'
            )
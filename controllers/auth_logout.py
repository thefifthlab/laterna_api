from odoo import http, fields
from odoo.http import request
import logging

_logger = logging.getLogger(__name__)


class LaternaAuthenticationLogout(http.Controller):

    @http.route('/api/v1/auth/logout', type='json', auth='public', methods=['POST'], csrf=False)
    def logout(self):
        """Handle logout using Odoo's built-in session management."""
        try:
            user = request.env.user
            ip_address = request.httprequest.remote_addr

            _logger.info(
                "Logout initiated - User: %s, IP: %s",
                user.login,
                ip_address
            )

            # Simply invalidate the Odoo session - this is the core logout
            request.session.logout(keep_db=True)

            _logger.info("Logout successful for user: %s", user.login)

            return {
                'status': 'success',
                'message': 'Logged out successfully'
            }

        except Exception as e:
            _logger.error(
                "Logout error - User: %s, IP: %s, Error: %s",
                user.login if user else 'Unknown',
                ip_address,
                str(e)
            )
            return {
                'status': 'error',
                'message': 'Logout failed'
            }
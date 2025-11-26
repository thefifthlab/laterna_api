from odoo import http
from odoo.http import request, Response
import json
import logging

_logger = logging.getLogger(__name__)


class MenuAPI(http.Controller):

    # ========== HELPER METHODS ==========
    def _json_response(self, data=None, status=200, message="Success"):
        """Standard JSON response format"""
        response_data = {
            'status': status,
            'message': message,
            'data': data or []
        }
        return Response(
            json.dumps(response_data, default=str),
            content_type='application/json',
            status=status
        )

    def _validate_required_fields(self, data, required_fields):
        """Validate required fields in request data"""
        missing_fields = [field for field in required_fields if field not in data]
        if missing_fields:
            return False, f"Missing required fields: {', '.join(missing_fields)}"
        return True, ""

    # ========== MAIN MENU ENDPOINTS ==========

    @http.route('/api/menus/main', type='http', auth='public', methods=['GET'], csrf=False)
    def get_main_menus(self, **kwargs):
        """Get all active main menus"""
        try:
            domain = [('active', '=', True)]

            # Search filter
            search = kwargs.get('search')
            if search:
                domain.append(('name', 'ilike', search))

            main_menus = request.env['product.template'].search(domain)

            result = []
            for menu in main_menus:
                result.append({
                    'id': menu.id,
                    'name': menu.name,
                    'hs_code': menu.hs_code,
                    'website_sequence': menu.website_sequence,
                    'description': menu.description
                })

            return self._json_response(data=result)

        except Exception as e:
            _logger.error(f"Error fetching main menus: {str(e)}")
            return self._json_response(status=500, message=str(e))

    @http.route('/api/menus/main/<int:menu_id>', type='http', auth='public', methods=['GET'], csrf=False)
    def get_main_menu(self, menu_id, **kwargs):
        """Get specific main menu with submenus and products"""
        try:
            menu = request.env['api.main.menu'].browse(menu_id)
            if not menu.exists() or not menu.active:
                return self._json_response(status=404, message='Main menu not found')

            # Get submenus
            submenus = []
            for submenu in menu.submenu_ids.filtered(lambda x: x.active):
                submenus.append({
                    'id': submenu.id,
                    'name': submenu.name,
                    'code': submenu.code,
                    'sequence': submenu.sequence,
                    'product_count': len(submenu.product_ids)
                })

            # Get direct products (products without submenu)
            direct_products = []
            for product in menu.product_ids.filtered(lambda x: x.active and not x.sub_menu_id):
                direct_products.append({
                    'id': product.id,
                    'name': product.name,
                    'code': product.code,
                    'price': product.price,
                    'display_price': product.display_price,
                    'is_featured': product.is_featured
                })

            data = {
                'id': menu.id,
                'name': menu.name,
                'code': menu.code,
                'sequence': menu.sequence,
                'description': menu.description,
                'submenus': submenus,
                'direct_products': direct_products
            }

            return self._json_response(data=data)

        except Exception as e:
            _logger.error(f"Error fetching main menu {menu_id}: {str(e)}")
            return self._json_response(status=500, message=str(e))

    # ========== SUB MENU ENDPOINTS ==========

    @http.route('/api/menus/sub', type='http', auth='public', methods=['GET'], csrf=False)
    def get_sub_menus(self, **kwargs):
        """Get all active sub menus with optional main menu filter"""
        try:
            domain = [('active', '=', True)]

            # Main menu filter
            main_menu_id = kwargs.get('main_menu_id')
            if main_menu_id:
                domain.append(('main_menu_id', '=', int(main_menu_id)))

            # Search filter
            search = kwargs.get('search')
            if search:
                domain.append(('name', 'ilike', search))

            sub_menus = request.env['api.sub.menu'].search(domain)

            result = []
            for submenu in sub_menus:
                result.append({
                    'id': submenu.id,
                    'name': submenu.name,
                    'code': submenu.code,
                    'sequence': submenu.sequence,
                    'main_menu': {
                        'id': submenu.main_menu_id.id,
                        'name': submenu.main_menu_id.name,
                        'code': submenu.main_menu_id.code
                    },
                    'product_count': len(submenu.product_ids)
                })

            return self._json_response(data=result)

        except Exception as e:
            _logger.error(f"Error fetching sub menus: {str(e)}")
            return self._json_response(status=500, message=str(e))

    @http.route('/api/menus/sub/<int:submenu_id>', type='http', auth='public', methods=['GET'], csrf=False)
    def get_sub_menu(self, submenu_id, **kwargs):
        """Get specific sub menu with products"""
        try:
            submenu = request.env['api.sub.menu'].browse(submenu_id)
            if not submenu.exists() or not submenu.active:
                return self._json_response(status=404, message='Sub menu not found')

            # Get products
            products = []
            for product in submenu.product_ids.filtered(lambda x: x.active):
                products.append({
                    'id': product.id,
                    'name': product.name,
                    'code': product.code,
                    'price': product.price,
                    'display_price': product.display_price,
                    'description': product.description,
                    'is_featured': product.is_featured,
                    'quantity_available': product.quantity_available
                })

            data = {
                'id': submenu.id,
                'name': submenu.name,
                'code': submenu.code,
                'sequence': submenu.sequence,
                'description': submenu.description,
                'main_menu': {
                    'id': submenu.main_menu_id.id,
                    'name': submenu.main_menu_id.name,
                    'code': submenu.main_menu_id.code
                },
                'products': products
            }

            return self._json_response(data=data)

        except Exception as e:
            _logger.error(f"Error fetching sub menu {submenu_id}: {str(e)}")
            return self._json_response(status=500, message=str(e))

    # ========== PRODUCT ENDPOINTS ==========

    @http.route('/api/products', type='http', auth='public', methods=['GET'], csrf=False)
    def get_products(self, **kwargs):
        """Get all products with optional filters"""
        try:
            domain = [('active', '=', True)]

            # Filter by main menu
            main_menu_id = kwargs.get('main_menu_id')
            if main_menu_id:
                domain.append(('main_menu_id', '=', int(main_menu_id)))

            # Filter by sub menu
            sub_menu_id = kwargs.get('sub_menu_id')
            if sub_menu_id:
                domain.append(('sub_menu_id', '=', int(sub_menu_id)))

            # Search filter
            search = kwargs.get('search')
            if search:
                domain.append('|')
                domain.append(('name', 'ilike', search))
                domain.append(('code', 'ilike', search))

            # Featured products filter
            featured = kwargs.get('featured')
            if featured and featured.lower() == 'true':
                domain.append(('is_featured', '=', True))

            products = request.env['api.product'].search(domain)

            result = []
            for product in products:
                product_data = {
                    'id': product.id,
                    'name': product.name,
                    'code': product.code,
                    'price': product.price,
                    'display_price': product.display_price,
                    'description': product.description,
                    'is_featured': product.is_featured,
                    'quantity_available': product.quantity_available,
                    'sequence': product.sequence
                }

                # Include menu hierarchy
                if product.main_menu_id:
                    product_data['main_menu'] = {
                        'id': product.main_menu_id.id,
                        'name': product.main_menu_id.name,
                        'code': product.main_menu_id.code
                    }

                if product.sub_menu_id:
                    product_data['sub_menu'] = {
                        'id': product.sub_menu_id.id,
                        'name': product.sub_menu_id.name,
                        'code': product.sub_menu_id.code
                    }

                result.append(product_data)

            return self._json_response(data=result)

        except Exception as e:
            _logger.error(f"Error fetching products: {str(e)}")
            return self._json_response(status=500, message=str(e))

    @http.route('/api/products/<int:product_id>', type='http', auth='public', methods=['GET'], csrf=False)
    def get_product(self, product_id, **kwargs):
        """Get specific product details"""
        try:
            product = request.env['api.product'].browse(product_id)
            if not product.exists() or not product.active:
                return self._json_response(status=404, message='Product not found')

            data = {
                'id': product.id,
                'name': product.name,
                'code': product.code,
                'price': product.price,
                'display_price': product.display_price,
                'description': product.description,
                'is_featured': product.is_featured,
                'quantity_available': product.quantity_available,
                'sequence': product.sequence
            }

            # Include menu hierarchy
            if product.main_menu_id:
                data['main_menu'] = {
                    'id': product.main_menu_id.id,
                    'name': product.main_menu_id.name,
                    'code': product.main_menu_id.code
                }

            if product.sub_menu_id:
                data['sub_menu'] = {
                    'id': product.sub_menu_id.id,
                    'name': product.sub_menu_id.name,
                    'code': product.sub_menu_id.code
                }

            return self._json_response(data=data)

        except Exception as e:
            _logger.error(f"Error fetching product {product_id}: {str(e)}")
            return self._json_response(status=500, message=str(e))

    # ========== COMPLETE HIERARCHY ENDPOINT ==========

    @http.route('/api/menu-hierarchy', type='http', auth='public', methods=['GET'], csrf=False)
    def get_complete_hierarchy(self, **kwargs):
        """Get complete menu hierarchy with all products"""
        try:
            main_menus = request.env['api.main.menu'].search([('active', '=', True)])

            result = []
            for main_menu in main_menus:
                main_menu_data = {
                    'id': main_menu.id,
                    'name': main_menu.name,
                    'code': main_menu.code,
                    'sequence': main_menu.sequence,
                    'submenus': [],
                    'direct_products': []
                }

                # Add submenus with their products
                for submenu in main_menu.submenu_ids.filtered(lambda x: x.active):
                    submenu_data = {
                        'id': submenu.id,
                        'name': submenu.name,
                        'code': submenu.code,
                        'sequence': submenu.sequence,
                        'products': []
                    }

                    # Add products for this submenu
                    for product in submenu.product_ids.filtered(lambda x: x.active):
                        submenu_data['products'].append({
                            'id': product.id,
                            'name': product.name,
                            'code': product.code,
                            'price': product.price,
                            'display_price': product.display_price,
                            'is_featured': product.is_featured
                        })

                    main_menu_data['submenus'].append(submenu_data)

                # Add direct products (without submenu)
                for product in main_menu.product_ids.filtered(lambda x: x.active and not x.sub_menu_id):
                    main_menu_data['direct_products'].append({
                        'id': product.id,
                        'name': product.name,
                        'code': product.code,
                        'price': product.price,
                        'display_price': product.display_price,
                        'is_featured': product.is_featured
                    })

                result.append(main_menu_data)

            return self._json_response(data=result)

        except Exception as e:
            _logger.error(f"Error fetching menu hierarchy: {str(e)}")
            return self._json_response(status=500, message=str(e))
from odoo import http
from odoo.http import request, Response
import json
import math
from odoo.exceptions import AccessError, ValidationError  # Import AccessError and ValidationError
import logging

_logger = logging.getLogger(__name__)


class ProductAPI(http.Controller):

    @http.route('/api/v1/products', type='http', auth='public', methods=['GET'], csrf=False, cors="*")
    def list_products(self, **kwargs):
        try:
            # Parse query parameters
            page = int(kwargs.get('page', 1))
            limit = int(kwargs.get('limit', 20))
            category_id = kwargs.get('category_id')
            search = kwargs.get('search')
            sort = kwargs.get('sort')

            # Build domain for published products
            domain = [('website_published', '=', True)]
            if category_id:
                domain.append(('public_categ_ids', 'in', [int(category_id)]))
            if search:
                domain.append(('name', 'ilike', search))

            # Determine sort order
            sort_order = 'id'
            if sort == 'price_asc':
                sort_order = 'list_price asc'
            elif sort == 'price_desc':
                sort_order = 'list_price desc'
            elif sort == 'name_desc':
                sort_order = 'name desc'

            # Search products
            products = request.env['product.template'].sudo().search(
                domain, offset=(page - 1) * limit, limit=limit, order=sort_order
            )

            # Get current website's pricelist for proper pricing
            website = request.env['website'].get_current_website()
            pricelist = website.pricelist_id

            # Prepare response data
            product_list = []
            for product in products:
                # Get price using pricelist rules
                price = self._get_product_price(product, pricelist)

                product_list.append({
                    'id': product.id,
                    'name': product.name,
                    'price': price,
                    'description': product.description or '',
                    'image_url': self._get_image_url(product),
                    'stock': product.qty_available if hasattr(product, 'qty_available') else 0,
                })

            total_count = request.env['product.template'].sudo().search_count(domain)
            total_pages = math.ceil(total_count / limit) if limit else 1

            response = {
                "products": product_list,
                "total": total_count,
                "pages": total_pages
            }

            return Response(json.dumps(response), status=200, content_type='application/json')

        except Exception as e:
            error = {'error': str(e)}
            return Response(json.dumps(error), status=500, content_type='application/json')

    def _get_product_price(self, product, pricelist):
        """Get product price considering pricelist rules"""
        # Use the product's list_price as fallback
        price = product.list_price

        try:
            # Try to get price from pricelist
            price = pricelist.get_product_price(product, 1, False)
        except Exception:
            # If that fails, try a different approach
            try:
                product_context = dict(request.env.context)
                product_context.update({
                    'pricelist': pricelist.id,
                    'quantity': 1
                })
                product = product.with_context(product_context)
                price = product.price
            except Exception:
                # Fall back to list_price if all else fails
                pass

        return price

    def _get_image_url(self, product):
        if product.image_1920:
            base_url = request.httprequest.host_url.strip('/')
            return f"{base_url}/web/image/product.template/{product.id}/image_1920/"
        return ''

    @http.route('/api/v1/products/<int:id>', type='http', auth='public', methods=['GET'], csrf=False, cors='*')
    def get_product_detail(self, id, **kwargs):
        """Get detailed information for a specific product"""
        if id <= 0:
            return request.make_json_response(
                {'error': 'Invalid product ID', 'error_code': 'invalid_id'},
                status=400
            )

        # Fetch product
        product = request.env['product.template'].sudo().search([
            ('id', '=', id)
        ], limit=1)

        if not product:
            return request.make_json_response(
                {'error': 'Product not found', 'error_code': 'not_found'},
                status=404
            )

        # Get default pricelist
        pricelist = request.env['product.pricelist'].sudo().search([], limit=1)
        if not pricelist:
            return request.make_json_response(
                {'error': 'Pricelist not found', 'error_code': 'no_pricelist'},
                status=500
            )

        product = product.with_context(pricelist=pricelist.id)
        price = product.price

        # Get variants
        variants = request.env['product.product'].sudo().search([
            ('product_tmpl_id', '=', id)
        ])
        variants_data = [{
            'id': variant.id,
            'attributes': {
                attribute.attribute_id.name: attribute.name
                for attribute in variant.product_template_attribute_value_ids
            }
        } for variant in variants]

        # Get ratings (reviews)
        reviews = request.env['rating.rating'].sudo().search([
            ('res_model', '=', 'product.template'),
            ('res_id', '=', id),
            ('consumed', '=', True)
        ])
        reviews_data = [{
            'rating': review.rating,
            'comment': review.feedback or ''
        } for review in reviews]

        # Get images
        images = []
        if product.image_1920:
            images.append(f'/web/image/product.template/{id}/image_1920')
        for image in product.product_template_image_ids:
            images.append(f'/web/image/product.image/{image.id}/image_1920')

        response = {
            'id': product.id,
            'name': product.name,
            'price': price,
            'description': product.description or '',
            'images': images,
            'variants': variants_data,
            'reviews': reviews_data
        }

        return request.make_json_response(response)

    @http.route('/api/v1/categories', type='http', auth='public', methods=['GET'], csrf=False, cors='*')
    def list_categories(self, **kwargs):
        """List categories or subcategories based on parent_id."""
        try:
            parent_id = kwargs.get('parent_id')
            domain = []
            # Add website filter only if request.website exists
            if hasattr(request, 'website'):
                domain.append(('website_id', 'in', (False, request.website.id)))
            else:
                _logger.info("No website context available; skipping website_id filter")

            if parent_id:
                try:
                    parent_id = int(parent_id)
                    parent = request.env['product.public.category'].sudo().browse(parent_id)
                    if not parent.exists():
                        _logger.warning("Parent category not found for parent_id %d", parent_id)
                        return http.Response(
                            json.dumps({'error': 'Parent category not found'}),
                            status=404,
                            mimetype='application/json'
                        )
                    domain.append(('parent_id', '=', parent_id))
                except ValueError:
                    _logger.warning("Invalid parent_id format: %s", parent_id)
                    return http.Response(
                        json.dumps({'error': 'Invalid parent_id format'}),
                        status=400,
                        mimetype='application/json'
                    )
            else:
                domain.append(('parent_id', '=', False))

            categories = request.env['product.public.category'].sudo().search(domain)
            _logger.info("Found %d categories for parent_id %s", len(categories), parent_id or 'None')

            result = {
                'categories': [
                    {
                        'id': category.id,
                        'name': category.with_context(lang=request.env.context.get('lang', 'en_US')).name,
                        'parent_id': category.parent_id.id if category.parent_id else None,
                        'image_url': f"/web/image/product.public.category/{category.id}/image_1920" if category.image_1920 else '',
                        'description': getattr(category, 'description', '') or '',
                        'product_count': request.env['product.template'].sudo().search_count([
                            ('public_categ_ids', 'in', [category.id])
                        ])
                    }
                    for category in categories
                ]
            }

            return http.Response(
                json.dumps(result),
                status=200,
                mimetype='application/json'
            )
        except AccessError:
            _logger.warning("AccessError in list_categories for parent_id %s", parent_id or 'None')
            return http.Response(
                json.dumps({'error': 'Access denied'}),
                status=403,
                mimetype='application/json'
            )
        except Exception as e:
            _logger.error("Error in list_categories for parent_id %s: %s", parent_id or 'None', str(e))
            return http.Response(
                json.dumps({'error': f'Internal server error: {str(e)}'}),
                status=500,
                mimetype='application/json'
            )

    @http.route('/api/v1/subcategories', type='http', auth='public', methods=['GET'], csrf=False, cors='*')
    def get_subcategories(self, parent_id=None, limit=100, **kwargs):
        """
        Fetch subcategories (child categories) optionally under a parent.
        - parent_id: Optional ID of parent category (e.g., for 'Furniture').
        - limit: Maximum number of root-level categories to return (default: 100).
        - Returns: JSON response with list of dicts containing id, name, parent_id as [id, name], and children (recursive hierarchy).
        """
        # Validate and convert parent_id
        try:
            parent_id = int(parent_id) if parent_id else None
        except (ValueError, TypeError):
            return request.make_json_response({'error': 'Invalid parent_id format'}, status=400)

        # Fetch ALL categories once for efficiency
        all_categories = request.env['product.public.category'].sudo().search_read(
            fields=['id', 'name', 'parent_id'],
            order='name'
        )

        if not all_categories:
            return request.make_json_response({'error': 'No categories found'}, status=404)

        # Build complete parent_data map: {parent_id: name} for all categories
        parent_data = {cat['id']: cat['name'] for cat in all_categories}

        # Build children_map: {parent_id: [child_cats]} for fast lookups
        children_map = {}
        for cat in all_categories:
            parent_id_val = cat.get('parent_id')
            if parent_id_val:
                parent_id_int = parent_id_val[0]
                if parent_id_int not in children_map:
                    children_map[parent_id_int] = []
                children_map[parent_id_int].append(cat)
            # Top-level have no entry in children_map

        # Determine root categories based on parent_id and limit
        if parent_id:
            root_categories = children_map.get(parent_id, [])
        else:
            root_categories = [cat for cat in all_categories if not cat.get('parent_id')]

        root_categories = root_categories[:int(limit)]  # Apply limit to roots

        if not root_categories:
            error_msg = 'No subcategories found' if parent_id else 'No top-level categories found'
            return request.make_json_response({'error': error_msg}, status=404)

        # Recursive builder using pre-built maps
        def build_hierarchy(cat):
            # Format parent_id as [id, name]
            parent_id_val = cat.get('parent_id')
            if parent_id_val:
                parent_name = parent_data.get(parent_id_val[0], 'Unknown')
                cat['parent_id'] = [parent_id_val[0], parent_name]
            else:
                cat['parent_id'] = False

            # Get and recurse children
            child_cats = children_map.get(cat['id'], [])
            cat['children'] = [build_hierarchy(child) for child in child_cats]

            # Prune to desired fields
            return {
                'id': cat['id'],
                'name': cat['name'],
                'parent_id': cat['parent_id'],
                'children': cat['children']
            }

        # Build hierarchy for roots
        hierarchy = [build_hierarchy(cat) for cat in root_categories]
        return request.make_json_response(hierarchy, status=200)

    @http.route('/api/product_details/<int:product_id>', type='json', auth='public', methods=['GET'], csrf=False,
                cors='*')
    def get_product_details(self, product_id, **kwargs):
        """
        Fetch detailed product info by ID.
        - product_id: ID of product.template.
        - Returns: Dict with name, price, variants (e.g., legs: steel/aluminum), images, etc.
        """
        product = request.env['product.template'].sudo().browse(product_id)
        if not product.exists():
            return {'error': 'Product not found'}

        # Fetch variants/attributes (e.g., legs material, color)
        attributes = []
        for attr_line in product.attribute_line_ids:
            attr_values = [{'id': v.id, 'name': v.name} for v in attr_line.value_ids]
            attributes.append({
                'attribute': attr_line.attribute_id.name,  # e.g., 'Legs', 'Color'
                'values': attr_values,  # e.g., [{'id':1, 'name':'Steel'}, {'id':2, 'name':'Aluminum'}]
                'price_extra': attr_line.value_price_extra  # e.g., +$50.40 for Aluminum
            })

        # Base price and image
        image_url = '/web/image/product.template/%s/image_1920' % product_id if product.image_1920 else False

        return {
            'id': product.id,
            'name': product.name,  # e.g., 'Customizable Desk'
            'base_price': product.list_price,  # e.g., 750.00
            'description': product.description_sale,
            'image_url': image_url,
            'attributes': attributes,  # Customizable options
            'in_stock': product.qty_available > 0,
            'website_url': product.website_url if hasattr(product, 'website_url') else False
        }

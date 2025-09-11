from odoo import http
from odoo.http import request, Response
import json
import math


class ProductAPI(http.Controller):

    @http.route('/api/v1/products', type='http', auth='public', methods=['GET'], csrf=False)
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

            # Set CORS headers
            headers = [
                ('Content-Type', 'application/json'),
                ('Access-Control-Allow-Origin', '*'),  # OR restrict to your domain
                ('Access-Control-Allow-Methods', 'GET, OPTIONS'),
                ('Access-Control-Allow-Headers', 'Content-Type'),
            ]

            return Response(json.dumps(response), status=200, headers=headers)

        except Exception as e:
            error = {'error': str(e)}
            headers = [
                ('Content-Type', 'application/json'),
                ('Access-Control-Allow-Origin', '*'),
            ]
            return Response(json.dumps(error), status=500, headers=headers)

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

    @http.route('/api/v1/products/<int:id>', type='http', auth='public', methods=['GET'], csrf=False)
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

    @http.route('/api/v1/categories', type='http', auth='public', methods=['GET'], cors='*')
    def list_categories(self, **kwargs):
        parent_id = kwargs.get('parent_id')
        domain = [('parent_id', '=', int(parent_id))] if parent_id else [('parent_id', '=', False)]

        categories = request.env['product.public.category'].sudo().search(domain)
        result = {
            'categories': [
                {
                    'id': category.id,
                    'name': category.name,
                    'parent_id': category.parent_id.id if category.parent_id else None,
                    # 'image_url': category.image_url if hasattr(category, 'image_url') else ''
                }
                for category in categories
            ]
        }

        return http.Response(
            json.dumps(result),
            status=200,
            mimetype='application/json'
        )

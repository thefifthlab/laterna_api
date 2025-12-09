from odoo import http
from odoo.http import request, Response
import json
import math
from odoo.exceptions import AccessError, ValidationError  # Import AccessError and ValidationError
import logging

_logger = logging.getLogger(__name__)


class ProductAPI(http.Controller):

    @http.route('/api/v1/products', type='json', auth='public', csrf=False, methods=['GET'], cors='*', )
    def list_products(self, **kwargs):
        """
        Public product catalogue endpoint.
        Query-string parameters:
            page (int)          – default 1
            limit (int)         – 1-100, default 20
            category_id (int)   – root public category
            search (str)        – free-text on name / sale description
            sort (str)          – price_asc|price_desc|name_asc|name_desc|newest
        """
        try:
            # ------------------------------
            # 1. Input validation
            # ------------------------------
            page = max(1, int(kwargs.get('page', 1)))
            limit = min(100, max(1, int(kwargs.get('limit', 20))))
            category_id = kwargs.get('category_id')
            search = (kwargs.get('search') or '').strip()[:100]
            sort = kwargs.get('sort')

            # ------------------------------
            # 2. Base domain
            # ------------------------------
            domain = [('website_published', '=', True), ('sale_ok', '=', True)]
            if category_id and str(category_id).isdigit():
                domain += [('public_categ_ids', 'child_of', int(category_id))]
            if search:
                domain += ['|', ('name', 'ilike', search), ('description_sale', 'ilike', search)]

            # ------------------------------
            # 3. Sorting
            # ------------------------------
            sort_mapping = {
                'price_asc': 'list_price asc',
                'price_desc': 'list_price desc',
                'name_asc': 'name asc',
                'name_desc': 'name desc',
                'newest': 'create_date desc',
            }
            order = sort_mapping.get(sort, 'website_sequence DESC, id DESC')

            # ------------------------------
            # 4. Search products
            # ------------------------------
            ProductTemplate = request.env['product.template']
            products = ProductTemplate.search(domain, offset=(page - 1) * limit, limit=limit, order=order)
            total = ProductTemplate.search_count(domain)

            # ------------------------------
            # 5. Pricelist & website context
            # ------------------------------
            website = request.env['website'].get_current_website()
            pricelist = website.pricelist_id or request.env['product.pricelist'].search([('active', '=', True)],
                                                                                        limit=1)
            base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url', default='').rstrip('/')

            # ------------------------------
            # 6. Build product payload
            # ------------------------------
            def get_product_price(tmpl):
                """Compute product price considering pricelist."""
                price = tmpl.list_price
                if pricelist and tmpl.product_variant_ids:
                    variant = tmpl.product_variant_id
                    if variant:
                        try:
                            price = pricelist.get_product_price(variant, 1, partner=False)
                        except Exception as exc:
                            _logger.warning("Pricelist error for %s (tmpl %s): %s", tmpl.name, tmpl.id, exc)
                return round(price, 2)

            def get_image_url(tmpl):
                return f"{base_url}/web/image/product.template/{tmpl.id}/image_1920" if tmpl.image_1920 else ''

            product_list = []
            for tmpl in products:
                price = get_product_price(tmpl)
                full_name = tmpl.display_name
                categories = [c.display_name for c in
                              tmpl.public_categ_ids.sorted('sequence')] if tmpl.public_categ_ids else []
                primary_category = categories[0] if categories else ''

                # Compute stock safely
                stock_qty = sum(
                    v.sudo().qty_available for v in tmpl.product_variant_ids) if tmpl.product_variant_ids else 0
                in_stock = stock_qty > 0

                product_list.append({
                    'id': tmpl.id,
                    'name': full_name,
                    'price': price,
                    'currency': pricelist.currency_id.name if pricelist and pricelist.currency_id else 'USD',
                    'description': tmpl.website_description or tmpl.description_sale or '',
                    'image_url': get_image_url(tmpl),
                    'stock': int(stock_qty),
                    'in_stock': in_stock,
                    'primary_category': primary_category,
                    'categories': categories,
                    'category_ids': tmpl.public_categ_ids.ids,
                })

            # ------------------------------
            # 7. Build JSON response
            # ------------------------------
            payload = {
                "success": True,
                "products": product_list,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": math.ceil(total / limit) if limit else 1,
                },
            }
            return payload

        except ValueError as ve:
            _logger.warning("Invalid request parameters: %s", ve)
            return {"success": False, "error": "Invalid parameters"}

        except Exception as exc:
            _logger.exception("Unexpected error in list_products")
            return {"success": False, "error": "Internal server error"}

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

    @http.route('/api/v1/product_details/<int:product_id>', type='json', auth='public', methods=['GET'], csrf=False, cors='*')
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
            'out_of_stock_message': product.out_of_stock_message,
            'website_url': product.website_url if hasattr(product, 'website_url') else False
        }

    @http.route('/api/v1/products/assign', type='json', auth='user', methods=['POST'], csrf=False, cors='*')
    def assign_products_to_category(self, **kwargs):
        """
        Assign products to a category/subcategory.
        - JSON Body: {'product_ids': [1,2], 'category_ids': [198,199], 'replace': true}  # replace=True clears old assignments
        - Auth: User/API key required (e.g., Authorization: Bearer <key>).
        - Returns: {'success': true, 'assigned': [(prod_id, cat_ids)]}
        """
        try:
            # Parse JSON body
            body = json.loads(request.httprequest.data.decode('utf-8'))
            product_ids = body.get('product_ids', [])
            category_ids = body.get('category_ids', [])
            replace = body.get('replace', True)  # Default: replace all cats

            if not product_ids or not category_ids:
                return request.make_json_response({'error': 'Missing product_ids or category_ids'}, status=400)

            # Validate IDs exist
            valid_prods = request.env['product.template'].sudo().browse(product_ids).exists()
            valid_cats = request.env['product.public.category'].sudo().browse(category_ids).exists()
            if len(valid_prods) != len(product_ids) or len(valid_cats) != len(category_ids):
                return request.make_json_response({'error': 'Some IDs not found'}, status=404)

            # Assign (batch write for efficiency)
            assigned = []
            for prod in valid_prods:
                cmd = (6, 0, category_ids.ids) if replace else [(4, cid) for cid in category_ids.ids]
                prod.sudo().write({'public_categ_ids': cmd})
                assigned.append((prod.id, category_ids.ids))

            return request.make_json_response({'success': True, 'assigned': assigned}, status=200)

        except json.JSONDecodeError:
            return request.make_json_response({'error': 'Invalid JSON body'}, status=400)
        except Exception as e:
            return request.make_json_response({'error': str(e)}, status=500)

    @http.route('/api/v1/products/by_subcategory',
                type='json', auth='public', methods=['POST'], csrf=False, cors='*')
    def get_products_by_parent_and_subcategory(self, **kwargs):
        """
        Fetch all products under a given parent category and subcategory.
        Example body:
        {
            "parent_id": 10,
            "subcategory_id": 25
        }
        """
        try:
            # Safely get the JSON body
            data = request.jsonrequest if hasattr(request, 'jsonrequest') else request.httprequest.get_json(force=True)

            parent_id = data.get('parent_id')
            subcategory_id = data.get('subcategory_id')

            if not parent_id or not subcategory_id:
                return {'error': 'Both parent_id and subcategory_id are required.'}

            parent = request.env['product.public.category'].sudo().browse(parent_id)
            subcategory = request.env['product.public.category'].sudo().browse(subcategory_id)

            # Validate existence
            if not parent.exists():
                return {'error': f'Parent category ID {parent_id} not found.'}
            if not subcategory.exists():
                return {'error': f'Subcategory ID {subcategory_id} not found.'}

            # Ensure subcategory belongs to parent
            if subcategory.parent_id.id != parent.id:
                return {'error': f'Subcategory "{subcategory.name}" does not belong to parent "{parent.name}".'}

            # Search products under subcategory
            products = request.env['product.template'].sudo().search([
                ('public_categ_ids', 'child_of', subcategory.id),
                ('sale_ok', '=', True),
                ('website_published', '=', True),
            ])

            result = [{
                'id': p.id,
                'name': p.name,
                'price': p.list_price,
                'currency': p.currency_id.name,
                'subcategory': subcategory.name,
                'parent_category': parent.name,
                'description': p.website_description or '',
                'image_url': f"/web/image/product.template/{p.id}/image_1920",
                'available_in_stock': p.qty_available,
            } for p in products]

            return {
                'parent_category': {'id': parent.id, 'name': parent.name},
                'subcategory': {'id': subcategory.id, 'name': subcategory.name},
                'total_products': len(result),
                'products': result,
            }

        except Exception as e:
            _logger.exception("Error fetching products by subcategory")
            return {'error': str(e)}

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

        @http.route('/api/v2/products', type='http', auth='public', methods=['GET'], csrf=False, cors="*")
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

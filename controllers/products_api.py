from odoo import http
from odoo.http import request, Response
import json
import math
from odoo.exceptions import AccessError, ValidationError  # Import AccessError and ValidationError
import logging

_logger = logging.getLogger(__name__)


class ProductAPI(http.Controller):
    @http.route(
        '/api/v1/products',
        type='http',
        auth='public',
        csrf=False,
        methods=['GET'],
        cors='*'
    )
    def list_products(self, **kwargs):
        """
        Public product catalogue endpoint (pure JSON via HTTP - no jsonrpc wrapper).

        Query parameters:
            page (int)          default: 1
            limit (int)         1–100, default: 20
            category_id (int)   public category root ID
            search (str)        search in name or sale description
            sort (str)          price_asc | price_desc | name_asc | name_desc | newest

        Returns clean JSON with products, prices (via pricelist), and pagination.
        """
        try:
            # 1. Input validation
            page = max(1, int(kwargs.get('page', 1)))
            limit = min(100, max(1, int(kwargs.get('limit', 20))))
            category_id_str = kwargs.get('category_id')
            search = (kwargs.get('search') or '').strip()[:100]
            sort = kwargs.get('sort')

            category_id = None
            if category_id_str and category_id_str.isdigit():
                category_id = int(category_id_str)

            # 2. Domain for published & sellable products
            domain = [
                ('website_published', '=', True),
                ('sale_ok', '=', True),
            ]

            if category_id:
                domain.append(('public_categ_ids', 'child_of', category_id))

            if search:
                domain.extend([
                    '|',
                    ('name', 'ilike', search),
                    ('description_sale', 'ilike', search),
                ])

            # 3. Sorting
            sort_mapping = {
                'price_asc': 'list_price asc',
                'price_desc': 'list_price desc',
                'name_asc': 'name asc',
                'name_desc': 'name desc',
                'newest': 'create_date desc',
            }
            order = sort_mapping.get(sort, 'website_sequence DESC, id DESC')

            # 4. Fetch products & count
            ProductTemplate = request.env['product.template'].sudo()
            products = ProductTemplate.search(
                domain,
                offset=(page - 1) * limit,
                limit=limit,
                order=order
            )
            total_count = ProductTemplate.search_count(domain)

            # 5. Website & pricelist – FIXED for Odoo 18
            website = request.env['website'].get_current_website()

            # Preferred / most reliable in Odoo 17–18 for website context
            pricelist = website.pricelist_id

            # Better: use the full context-aware helper (handles country, geoip, user, etc.)
            if not pricelist:
                pricelist = request.env['product.pricelist'].sudo()._get_current_pricelist()

            # Ultimate fallback: any active pricelist tied to website or global
            if not pricelist:
                pricelist = request.env['product.pricelist'].sudo().search([
                    ('active', '=', True),
                    '|',
                    ('website_id', '=', website.id),
                    ('website_id', '=', False),
                ], order='sequence', limit=1)

            # If still nothing → fallback to list_price (no pricelist applied)
            use_pricelist = bool(pricelist)

            base_url = request.env['ir.config_parameter'].sudo().get_param(
                'web.base.url', ''
            ).rstrip('/')

            # 6. Helper functions
            def get_product_price(template):
                if not use_pricelist or not template.product_variant_ids:
                    return round(template.list_price, 2)

                try:
                    variant = template.product_variant_id  # main variant for preview
                    # This is the stable method used in controllers / website_sale
                    price = pricelist._get_product_price(
                        product=variant,
                        quantity=1,
                        uom=variant.uom_id or template.uom_id,
                        date=False,
                        partner=False
                    )
                    return round(price, 2)
                except Exception as exc:
                    _logger.warning(
                        "Price failed for template %s (%s): %s",
                        template.id, template.name, exc
                    )
                    return round(template.list_price, 2)

            def get_image_url(template):
                if not template.image_1920:
                    return ''
                return f"{base_url}/web/image/product.template/{template.id}/image_1920"

            # 7. Build product list
            product_data = []
            for tmpl in products:
                product_data.append({
                    'id': tmpl.id,
                    'name': tmpl.name,
                    'description_short': (tmpl.description_sale or '').strip()[:280],
                    'price': get_product_price(tmpl),
                    'currency': pricelist.currency_id.name if use_pricelist and pricelist.currency_id else 'USD',
                    'image_url': get_image_url(tmpl),
                    'public_category_ids': tmpl.public_categ_ids.ids,
                    'website_sequence': tmpl.website_sequence,
                    'create_date': tmpl.create_date.strftime('%Y-%m-%d') if tmpl.create_date else None,
                    # Uncomment extras if needed:
                    # 'default_code': tmpl.default_code or '',
                    # 'product_url': f"{base_url}/shop/product/{tmpl.id}" if tmpl.website_published else '',
                })

            # 8. Final payload
            response_payload = {
                'status': 'success',
                'data': product_data,
                'pagination': {
                    'current_page': page,
                    'per_page': limit,
                    'total_items': total_count,
                    'total_pages': (total_count + limit - 1) // limit if limit > 0 else 1,
                },
                'meta': {
                    'applied_filters': {
                        'search': search or None,
                        'category_id': category_id,
                        'sort': sort or 'default (website sequence)',
                    },
                    'pricelist_used': pricelist.name if use_pricelist else 'No pricelist (using base price)',
                }
            }

            return request.make_response(
                json.dumps(response_payload, ensure_ascii=False),
                headers={
                    'Content-Type': 'application/json; charset=utf-8',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'GET, OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
                },
                status=200
            )

        except ValueError as ve:
            return request.make_response(
                json.dumps({'status': 'error', 'message': f'Invalid input: {str(ve)}', 'code': 400}),
                headers={'Content-Type': 'application/json; charset=utf-8'},
                status=400
            )

        except Exception as e:
            _logger.error("Error in /api/v1/products: %s", str(e), exc_info=True)
            return request.make_response(
                json.dumps({'status': 'error', 'message': 'Internal server error', 'code': 500}),
                headers={'Content-Type': 'application/json; charset=utf-8'},
                status=500
            )

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

    @http.route('/api/v1/product_details/<int:product_id>',
                type='http',
                auth='public',
                methods=['POST'],
                csrf=False,
                cors='*')
    def get_product_details(self, product_id, **kwargs):
        """
        Fetch detailed product info by ID (now using type='http' for full response control).
        Expects JSON body in POST (if any), returns clean JSON.
        """
        product = request.env['product.template'].sudo().browse(product_id)
        if not product.exists():
            error_data = {'error': 'Product not found'}
            return request.make_response(
                json.dumps(error_data),
                status=404,
                headers={'Content-Type': 'application/json'}
            )

        # ── Attributes with correct per-value price_extra ──
        attributes = []
        for line in product.attribute_line_ids:
            values = []
            for value in line.value_ids:
                link = request.env['product.template.attribute.value'].sudo().search([
                    ('product_tmpl_id', '=', product.id),
                    ('attribute_id', '=', line.attribute_id.id),
                    ('product_attribute_value_id', '=', value.id),
                ], limit=1)
                price_extra = link.price_extra if link else 0.0

                values.append({
                    'id': value.id,
                    'name': value.name,
                    'price_extra': price_extra,
                    'is_custom': value.is_custom,
                })

            attributes.append({
                'attribute_id': line.attribute_id.id,
                'attribute_name': line.attribute_id.name,
                'display_type': line.attribute_id.display_type,
                'values': values,
                'default_value_id': line.default_value_id.id if line.default_value_id else False,
            })

        # Image fallback
        image_url = False
        if product.image_1920:
            image_url = f'/web/image/product.template/{product.id}/image_1920'
        elif product.product_image_ids:  # more reliable in recent versions
            image_url = f'/web/image/product.image/{product.product_image_ids[0].id}/image_1920'

        # Build response data
        data = {
            'id': product.id,
            'name': product.name,
            'base_price': product.list_price,
            'description': product.description_sale,
            'image_url': image_url,
            'attributes': attributes,
            'in_stock': product.virtual_available > 0,  # better than qty_available for most cases
            'out_of_stock_message': product.out_of_stock_message or "Out of stock",
        }

        # Return clean JSON with 200 OK
        return request.make_response(
            json.dumps(data),
            headers={'Content-Type': 'application/json'}
        )

    @http.route(
        '/api/v1/products/assign',
        type='http',
        auth='user',  # Change to 'public' + API key validation if needed
        methods=['POST'],
        csrf=False,
        cors='*'
    )
    def assign_products_to_category(self, **kwargs):
        """
        Assign one or more products to one or more public website categories.

        POST /api/v1/products/assign
        Content-Type: application/json

        Example request body:
        {
            "product_ids": [15, 42, 100],
            "category_ids": [198, 199],
            "replace": true
        }

        - replace: true  → remove all existing categories and set only the new ones (default)
        - replace: false → add the new categories without removing existing ones

        Successful response (200 OK):
        {
            "success": true,
            "message": "3 product(s) assigned to 2 category(ies)",
            "assigned": [
                [15, [198, 199]],
                [42, [198, 199]],
                [100, [198, 199]]
            ]
        }

        Error responses:
        - 400 Bad Request → missing/invalid input
        - 404 Not Found   → one or more IDs do not exist
        - 500 Server Error → unexpected exception
        """
        # 1. Parse JSON body safely
        try:
            body = request.jsonrequest
        except Exception:
            try:
                body = json.loads(request.httprequest.data.decode('utf-8'))
            except json.JSONDecodeError:
                return request.make_json_response(
                    {'error': 'Invalid or missing JSON body. Please send valid JSON.'},
                    status=400
                )

        # 2. Extract and validate required fields
        product_ids = body.get('product_ids', [])
        category_ids = body.get('category_ids', [])
        replace = body.get('replace', True)  # default: replace existing assignments

        if not isinstance(product_ids, list) or not isinstance(category_ids, list):
            return request.make_json_response(
                {'error': 'product_ids and category_ids must be arrays (lists) of integers'},
                status=400
            )

        if not product_ids or not category_ids:
            return request.make_json_response(
                {'error': 'Both product_ids and category_ids are required and must be non-empty'},
                status=400
            )

        # 3. Load and validate records (using sudo() for API simplicity – consider removing in production)
        Product = request.env['product.template'].sudo()
        PublicCategory = request.env['product.public.category'].sudo()

        products = Product.browse(product_ids)
        categories = PublicCategory.browse(category_ids)

        # Early existence check
        if len(products.exists()) != len(product_ids):
            return request.make_json_response(
                {'error': 'One or more product_ids do not exist or are inaccessible'},
                status=404
            )

        if len(categories.exists()) != len(category_ids):
            return request.make_json_response(
                {'error': 'One or more category_ids do not exist or are inaccessible'},
                status=404
            )

        # 4. Prepare many2many command
        if replace:
            # Replace: clear old → set new ones
            command = Command.set(categories.ids)
        else:
            # Append: only link new ones
            command = [Command.link(cid) for cid in categories.ids]

        # 5. Perform the assignment (single write when possible)
        try:
            # Optimization: if replace=True or all products get the exact same command → bulk write
            products.write({'public_categ_ids': command})

            # Build response detail
            assigned = [[p.id, categories.ids] for p in products]

            return request.make_json_response({
                'success': True,
                'message': f"{len(products)} product(s) assigned to {len(categories)} category(ies)",
                'assigned': assigned
            }, status=200)

        except Exception as e:
            return request.make_json_response(
                {'error': f'Failed to assign categories: {str(e)}'},
                status=500
            )

    @http.route(
        '/api/v1/products/by_subcategory',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
        cors='*'
    )

    def get_products_by_parent_and_subcategory(self, **kwargs):
        """
        Fetch published products under a specific parent category + subcategory (hierarchical).

        POST /api/v1/products/by_subcategory
        Content-Type: application/json

        Request body example:
        {
            "parent_id": 10,
            "subcategory_id": 25
        }

        Response (200 OK):
        {
            "success": true,
            "parent_category": {"id": 10, "name": "Electronics"},
            "subcategory": {"id": 25, "name": "Smartphones"},
            "total_products": 12,
            "products": [
                {
                    "id": 42,
                    "name": "iPhone 14 Pro",
                    "list_price": 999.0,
                    "sale_price": 999.0,          // after pricelist if applied
                    "currency": "USD",
                    "description": "...",
                    "image_url": "/web/image/product.template/42/image_1920",
                    "slug": "iphone-14-pro",
                    "stock_quantity": 15,
                    "in_stock": true
                },
                ...
            ]
        }

        Errors:
        - 400 Bad Request (missing/invalid input)
        - 404 Not Found (category not exist or mismatch)
        - 500 Server Error
        """
        try:
            # Parse JSON body (preferred in Odoo 18+)
            try:
                data = request.jsonrequest
            except Exception:
                try:
                    data = request.httprequest.get_json(force=True)
                except:
                    return request.make_json_response(
                        {'error': 'Invalid or missing JSON body. Send valid JSON.'},
                        status=400
                    )

            parent_id = data.get('parent_id')
            subcategory_id = data.get('subcategory_id')

            if not parent_id or not subcategory_id:
                return request.make_json_response(
                    {'error': 'Both parent_id and subcategory_id are required (integer values).'},
                    status=400
                )

            # Ensure they are integers
            try:
                parent_id = int(parent_id)
                subcategory_id = int(subcategory_id)
            except (ValueError, TypeError):
                return request.make_json_response(
                    {'error': 'parent_id and subcategory_id must be valid integers.'},
                    status=400
                )

            Category = request.env['product.public.category'].sudo()
            Product = request.env['product.template'].sudo()

            parent = Category.browse(parent_id)
            subcategory = Category.browse(subcategory_id)

            if not parent.exists():
                return request.make_json_response(
                    {'error': f'Parent category ID {parent_id} not found.'},
                    status=404
                )

            if not subcategory.exists():
                return request.make_json_response(
                    {'error': f'Subcategory ID {subcategory_id} not found.'},
                    status=404
                )

            # Verify subcategory belongs to parent (or is descendant)
            if subcategory.parent_id.id != parent.id:
                # Optional: allow deeper nesting? If yes → use child_of on parent instead
                return request.make_json_response(
                    {'error': f'Subcategory "{subcategory.name}" does not belong to parent "{parent.name}".'},
                    status=400
                )

            # Domain: products in this subcategory or its children + published + saleable
            domain = [
                ('public_categ_ids', 'child_of', subcategory.id),
                ('sale_ok', '=', True),
                ('website_published', '=', True),
            ]

            # Optional: filter by current website if multi-website
            if request.website:
                domain.append(('website_id', 'in', [False, request.website.id]))

            products = Product.search(domain)

            # Optional: apply pricelist (website context)
            pricelist = request.website.get_current_pricelist() if request.website else None

            result = []
            for p in products:
                price = p.list_price
                if pricelist:
                    price = pricelist._get_product_price(p, quantity=1)

                image_url = False
                if p.image_1920:
                    image_url = f"/web/image/product.template/{p.id}/image_1920"
                # Fallback to first image if needed
                elif p.product_image_ids:
                    image_url = f"/web/image/product.image/{p.product_image_ids[0].id}/image_1920"

                result.append({
                    'id': p.id,
                    'name': p.name,
                    'list_price': p.list_price,
                    'sale_price': price if not float_is_zero(price - p.list_price,
                                                             precision_digits=2) else p.list_price,
                    'currency': p.currency_id.name or 'USD',
                    'description': p.website_description or p.description_sale or '',
                    'image_url': image_url,
                    'slug': p.website_slug if hasattr(p, 'website_slug') else False,
                    'stock_quantity': p.virtual_available,  # better than qty_available for most cases
                    'in_stock': p.virtual_available > 0,
                })

            return request.make_json_response({
                'success': True,
                'parent_category': {'id': parent.id, 'name': parent.name},
                'subcategory': {'id': subcategory.id, 'name': subcategory.name},
                'total_products': len(result),
                'products': result,
            }, status=200)

        except Exception as e:
            _logger.exception("Error in /api/v1/products/by_subcategory: %s", str(e))
            return request.make_json_response(
                {'error': 'Internal server error occurred.'},
                status=500
            )

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
                price, rule = pricelist._get_product_price_rule(
                    product,
                    quantity=qty or 1.0,
                    partner=partner,
                    date=False,  # or datetime if needed
                    uom_id=False  # or the uom
                )
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
                price, rule = pricelist._get_product_price_rule(
                    product,
                    quantity=qty or 1.0,
                    partner=partner,
                    date=False,  # or datetime if needed
                    uom_id=False  # or the uom
                )
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


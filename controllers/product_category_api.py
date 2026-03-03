# controllers/product_category_api.py
from odoo import http
from odoo.http import request
from odoo.tools import slug
import math


class ShopApiController(http.Controller):

    @http.route('/shop/category/<int:category_id>', type='json', auth='public', methods=['GET'], csrf=False)
    def get_category_products(self, category_id, **kwargs):
        """
        GET /shop/category/10
        Optional: ?page=1&per_page=24&sort=price_low_to_high&min_price=295&max_price=2100
        """
        try:
            # === 1. Input Validation ===
            page = max(1, int(kwargs.get('page', 1)))
            per_page = min(100, max(1, int(kwargs.get('per_page', 24))))  # Cap at 100
            sort = kwargs.get('sort', 'featured').lower()

            # Price filters (optional)
            min_price = None
            max_price = None
            if kwargs.get('min_price'):
                try:
                    min_price = float(kwargs.get('min_price'))
                except ValueError:
                    return {'success': False, 'error': 'Invalid min_price'}
            if kwargs.get('max_price'):
                try:
                    max_price = float(kwargs.get('max_price'))
                except ValueError:
                    return {'success': False, 'error': 'Invalid max_price'}

            # === 2. Fetch Category ===
            category = request.env['product.public.category'].browse(category_id)
            if not category.exists():
                return {'success': False, 'error': 'Category not found'}

            # === 3. Build Domain ===
            domain = [
                ('public_categ_ids', 'child_of', category.id),
                ('sale_ok', '=', True),
                ('active', '=', True),
            ]
            if min_price is not None:
                domain.append(('list_price', '>=', min_price))
            if max_price is not None:
                domain.append(('list_price', '<=', max_price))

            # === 4. Sorting ===
            order_map = {
                'featured': 'sequence desc, id desc',
                'price_low_to_high': 'list_price asc, id desc',
                'price_high_to_low': 'list_price desc, id desc',
                'newest': 'create_date desc, id desc',
            }
            order = order_map.get(sort, 'sequence desc, id desc')

            # === 5. Pagination & Count ===
            offset = (page - 1) * per_page
            products = request.env['product.template'].search(
                domain, order=order, limit=per_page, offset=offset
            )
            total = request.env['product.template'].search_count(domain)

            # === 6. Price Range (Fixed read_group) ===
            price_min = price_max = 0.0
            if total > 0:
                stats = request.env['product.template'].read_group(domain, ['list_price'], [])
                prices = [p['list_price'] for p in stats if p['list_price'] is not False]
                if prices:
                    price_min = round(min(prices), 2)
                    price_max = round(max(prices), 2)

            current_min = min_price if min_price is not None else price_min
            current_max = max_price if max_price is not None else price_max

            # === 7. Serialize Products ===
            base_url = request.httprequest.host_url.rstrip('/')
            user = request.env.user
            can_see_stock = user.has_group('stock.group_stock_user') or user.has_group('sales_team.group_sale_salesman')

            product_list = []
            for p in products:
                variant = p.product_variant_id  # Single variant or first
                in_stock = None
                if can_see_stock and variant:
                    try:
                        in_stock = variant.qty_available > 0
                    except Exception:
                        in_stock = None

                product_list.append({
                    'id': p.id,
                    'name': p.name,
                    'slug': slug(p),  # Uses Odoo 18 slug correctly
                    'price': round(p.list_price, 2),
                    'image': f"{base_url}/web/image/product.template/{p.id}/image_1024" if p.image_1920 else False,
                    'thumbnail': f"{base_url}/web/image/product.template/{p.id}/image_256" if p.image_1920 else False,
                    'in_stock': in_stock,
                })

            # === 8. Breadcrumbs ===
            breadcrumbs = [{'name': 'Shop', 'url': '/shop'}]
            current = category
            trail = []
            while current:
                trail.append({
                    'name': current.name,
                    'url': f'/shop/category/{current.id}'
                })
                current = current.parent_id
            breadcrumbs.extend(reversed(trail))

            # === 9. Final Response ===
            return {
                'success': True,
                'data': {
                    'category': {
                        'id': category.id,
                        'name': category.name,
                        'parent': category.parent_id.name if category.parent_id else None,
                        'breadcrumbs': breadcrumbs,
                    },
                    'filters': {
                        'price_range': {
                            'min': price_min,
                            'max': price_max,
                            'current_min': round(current_min, 2),
                            'current_max': round(current_max, 2),
                        },
                        'sort_options': [
                            'Featured',
                            'Price: Low to High',
                            'Price: High to Low',
                            'Newest'
                        ],
                    },
                    'products': product_list,
                    'pagination': {
                        'current_page': page,
                        'per_page': per_page,
                        'total': total,
                        'total_pages': math.ceil(total / per_page) if per_page > 0 else 0,
                    },
                }
            }

        except Exception as e:
            return {'success': False, 'error': str(e)}
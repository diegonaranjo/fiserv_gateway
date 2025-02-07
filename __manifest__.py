{
    'name': 'Fiserv Payment Gateway',
    'category': 'Accounting/Payment',
    'summary': 'Integrate Fiserv payment gateway Argentina.',
    'description': """
        This module integrates the Fiserv payment gateway with Odoo,
        allowing for secure payment processing in your e-commerce platform.
    """,
    'version': '18.0.1.0',
    'author': 'Diego Naranjo',
    'depends': ['base', 'sale', 'payment', 'portal', 'point_of_sale'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/payment_transaction_views.xml',
        'views/payment_provider_views.xml',
        'views/payment_form_templates.xml',
        'data/payment_provider_data.xml',
        'data/mail_template_data.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'sequence': 6,
    'assets': {
        'web.assets_frontend': [
            'fiserv_gateway/static/src/js/payment_form.js',
            'fiserv_gateway/static/src/scss/payment_form.scss',
        ],
         'web.assets_backend': [
            'fiserv_gateway/static/src/scss/payment_provider.scss',
            'fiserv_gateway/static/src/scss/pos_payment.scss',
        ],
        'point_of_sale._assets_pos': [
            'fiserv_gateway/static/src/xml/pos_payment_status_views.xml',
            'fiserv_gateway/static/src/js/pos_payment_screen.js',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'license': 'LGPL-3',
}

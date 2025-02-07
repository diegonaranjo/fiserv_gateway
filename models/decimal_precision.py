from odoo import api, models, fields
from odoo.addons import decimal_precision as dp

class DecimalPrecision(models.Model):
    _inherit = 'decimal.precision'

    @api.model
    def _register_hook(self):
        super()._register_hook()
        
        # Check if precision already exists to avoid unnecessary writes
        payment_precision = self.search([('name', '=', 'Payment')], limit=1)
        if not payment_precision:
            self.create({
                'name': 'Payment',
                'digits': 6
            })
        elif payment_precision.digits != 6:
            payment_precision.write({'digits': 6})

    def init(self):
        super().init()
        # Register precision only once during installation
        precision = self.search([('name', '=', 'Payment')], limit=1)
        if not precision:
            self.create({
                'name': 'Payment',
                'digits': 6
            })

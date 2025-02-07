from odoo import api, fields, models
from odoo.exceptions import ValidationError
import json
import os
from decimal import Decimal
from .. import const
import logging

_logger = logging.getLogger(__name__)

class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    fiserv_provider_id = fields.Many2one(
        'payment.provider',
        domain=[('code', '=', 'fiserv')],
        string='Proveedor Fiserv'
    )

    card_config_ids = fields.Many2many(
        'fiserv.card.config',
        string='Marcas de Tarjeta',
        domain=[('active', '=', True)],
        help='Tarjetas disponibles para este método de pago'
    )
        
    enable_installments = fields.Boolean(
        related='fiserv_provider_id.fiserv_enable_installments',
        string='Habilitar cuotas',
        readonly=False
    )
    
    fiserv_store_name = fields.Char(
        related='fiserv_provider_id.fiserv_store_name',
        readonly=True
    )
    
    fiserv_environment = fields.Selection(
        related='fiserv_provider_id.fiserv_environment',
        readonly=True
    )

    @api.model
    def _is_fiserv_method(self):
        return self.id == 6

    @api.constrains('id', 'card_config_ids')
    def _check_fiserv_card_brand(self):
        for record in self:
            if record.id == 6 and not record.card_config_ids:
                raise ValidationError('Debe seleccionar al menos una marca de tarjeta')

    @api.depends('fiserv_provider_id')
    def _compute_card_config(self):
        for record in self:
            if record.id == 6 and record.fiserv_provider_id:
                record.card_config_ids = self.env['fiserv.card.config'].search([
                    ('active', '=', True)
                ])
            else:
                record.card_config_ids = False

    def _get_payment_method_information(self):
        res = super()._get_payment_method_information()
        if self.id == 6:
            try:
                card_configs = self.card_config_ids.filtered('active')
                installment_data = {}
                
                for card in card_configs:
                    installments = []
                    for installment in card.installments.filtered('active'):
                        installments.append({
                            'id': installment.id,
                            'installments': str(installment.installments),
                            'coefficient': 1 + (installment.interest_rate / 100),
                            'interest_rate': installment.interest_rate,
                            'installment_to_send': installment.installment_to_send
                        })
                    
                    if installments:
                        installment_data[card.code] = {
                            'name': card.name,
                            'installments': installments
                        }
                
                res.update({
                    'payment_method_type': 'fiserv',
                    'enable_installments': self.enable_installments,
                    'available_cards': [{
                        'id': card.id,
                        'code': card.code,
                        'name': card.name,
                        'credit': card.credit,
                        'debit': card.debit
                    } for card in card_configs],
                    'installment_plans': installment_data
                })
                
            except Exception as e:
                _logger.error("Error getting Fiserv payment information: %s", str(e))
                
        return res

    def get_installments(self, payment_method_id):
        try:
            payment_method = request.env['pos.payment.method'].browse(int(payment_method_id))
            if payment_method.id != 6:
                return []
                
            # Get active card configurations and filter by enabled installments
            card_configs = request.env['fiserv.card.config'].sudo().search([
                ('active', '=', True),
                ('enable_installments', '=', True)
            ])
            
            # Log actual config for debugging
            _logger.info("Card configs found: %s", card_configs.mapped('code'))
            
            return card_configs.filtered(
                lambda c: c.installments and c.installments.filtered('active')
            ).mapped(lambda c: [c.code, c.name])
                
        except Exception as e:
            _logger.error("Error getting card brands: %s", str(e))
            return []
    
    def _validate_installment_payment(self, payment_data):
        """
        Validate installment payment data before processing.
        
        Args:
            payment_data (dict): Payment data including card_brand, installments, amount
            
        Returns:
            bool: True if validation passes
            
        Raises:
            ValidationError: If validation fails
        """
        if not self._is_fiserv_method():
            return True
            
        required_fields = ['card_brand', 'installments', 'amount']
        if not all(field in payment_data for field in required_fields):
            raise ValidationError('Missing required fields for installment payment')
            
        card_config = self.card_config_ids.filtered(
            lambda c: c.code == payment_data['card_brand'] and c.active
        )
        
        if not card_config:
            raise ValidationError('Invalid card brand')
            
        installment = card_config.installments.filtered(
            lambda i: str(i.installments) == str(payment_data['installments']) and i.active
        )
        
        if not installment:
            raise ValidationError('Invalid installment plan')
            
        return True

    def _log_payment_error(self, error_type, error_data):
        """
        Log payment processing errors for debugging.
        
        Args:
            error_type (str): Type of error
            error_data (dict): Error details
        """
        logger = self.env['fiserv.transaction.log'].sudo()
        log_data = {
            'payment_method_id': self.id,
            'error_type': error_type,
            'error_data': error_data,
            'timestamp': fields.Datetime.now()
        }
        logger.log_error(log_data)
    
    def _prepare_invoice_vals(self):
        """Agrega información de cuotas e intereses a la factura."""
        vals = super()._prepare_invoice_vals()
        
        card_payment = self.payment_ids.filtered(
            lambda p: p.payment_method_id.is_credit_card and p.installments > 1
        )
        
        if card_payment:
            card_brand_name = card_payment.fiserv_card_brands.name
            vals.update({
                'narration': f"""
                Pago con tarjeta: {card_brand_name}
                Cuotas: {card_payment.installments}
                Tasa de interés: {card_payment.interest_rate:.2f}%
                Total con interés: {card_payment.total_with_interest:.2f}
                """
            })
            
        return vals

    def _get_installment_amount(self, payment):
        """Calcula el monto de cada cuota."""
        if payment.installments > 1:
            return payment.total_with_interest / payment.installments
        return payment.amount
    
        
class PosPayment(models.Model):
    _inherit = 'pos.payment'

    card_config_id = fields.Many2one(
        'fiserv.card.config',
        string='Marca de tarjeta'
    )

    installments = fields.Integer(
        string='Cuotas',
        default=1
    )
    
    total_with_interest = fields.Monetary(
        string='Total con interés',
        currency_field='currency_id',
        compute='_compute_total_with_interest',
        store=True
    )
    
    interest_rate = fields.Float(
        string='Tasa de interés',
        digits=(16, 4),
        compute='_compute_interest_rate',
        store=True
    )
    
    @api.depends('card_config_id', 'installments', 'amount')
    def _compute_total_with_interest(self):
        for payment in self:
            if payment.installments > 1 and payment.card_config_id:
                installment = payment.card_config_id.installments.filtered(
                    lambda i: i.installments == payment.installments
                )
                if installment:
                    interest_rate = installment.interest_rate / 100
                    payment.total_with_interest = payment.amount * (1 + interest_rate)
                else:
                    payment.total_with_interest = payment.amount
            else:
                payment.total_with_interest = payment.amount

    @api.depends('card_config_id', 'installments')
    def _compute_interest_rate(self):
        for payment in self:
            if payment.installments > 1 and payment.card_config_id:
                installment = payment.card_config_id.installments.filtered(
                    lambda i: i.installments == payment.installments
                )
                payment.interest_rate = installment.interest_rate if installment else 0.0
            else:
                payment.interest_rate = 0.0
                
    def _update_payment_line_values(self, values):
        res = super()._update_payment_line_values(values)
        if values.get('card_config_id'):
            res.update({
                'card_config_id': values['card_config_id'],
                'installments': values.get('installments', 1)
            })
        return res
                     
class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'
    
    original_price = fields.Float(
        string='Precio Original',
        digits='Product Price',
        readonly=True,
        store=True,
        help='Precio original antes de aplicar intereses'
    )
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['original_price'] = vals.get('price_unit', 0.0)
        return super().create(vals_list)

    @api.depends('price_unit', 'order_id.payment_ids.interest_rate')
    def _compute_amount(self):
        for line in self:
            payment = line.order_id.payment_ids.filtered(
                lambda p: p.payment_method_id.id == 6 
                and p.installments > 1 
                and p.interest_rate > 0
            )
            if payment:
                price = Decimal(str(line.original_price or line.price_unit))
                interest = Decimal(str(1 + payment[0].interest_rate))
                line.price_unit = float(
                    (price * interest).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                )
            super(PosOrderLine, line)._compute_amount()
    
    def init_original_price(self):
        """Inicializa el precio original cuando se crea la línea"""
        self.original_price = self.price_unit
        return True

    @api.depends('price_unit', 'order_id.payment_ids.interest_rate')
    def _compute_price_with_interest(self):
        for line in self:
            payment = line.order_id.payment_ids.filtered(
                lambda p: p.payment_method_id.id == 6 and p.installments > 1
            )
            if payment and payment.interest_rate:
                coefficient = 1 + payment.interest_rate
                # Usar el precio original en lugar del price_unit actual
                line.price_with_interest = line.original_price * coefficient
            else:
                line.price_with_interest = line.original_price or line.price_unit       

    def _log_payment_error(self, error_type, error_data):
        """
        Log payment processing errors for debugging.
        
        Args:
            error_type (str): Type of error
            error_data (dict): Error details
        """
        logger = self.env['fiserv.transaction.log'].sudo()
        log_data = {
            'payment_method_id': self.id,
            'error_type': error_type,
            'error_data': error_data,
            'timestamp': fields.Datetime.now()
        }
        logger.log_error(log_data)                

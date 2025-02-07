import json
import logging
from .. import const
from odoo.http import request, Response
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from decimal import Decimal, ROUND_HALF_DOWN, ROUND_HALF_UP, getcontext, InvalidOperation

_logger = logging.getLogger(__name__)

getcontext().prec = 20

class FiservPrecisionMixin(models.AbstractModel):
    _name = 'fiserv.precision.mixin'
    _description = 'Mixin for handling decimal precision in Fiserv calculations'

    def _decimal_to_float(self, decimal_value):
        """
        Convert decimal value to float with proper validation and rounding.
        Handles None values and invalid decimals gracefully.
        """
        try:
            if not decimal_value:
                return 0.0
            if not isinstance(decimal_value, Decimal):
                decimal_value = self._str_to_decimal(decimal_value)
            return float(decimal_value.quantize(Decimal('.001'), rounding=ROUND_HALF_DOWN))
        except (InvalidOperation, ValueError, TypeError):
            _logger.warning("Invalid decimal value for conversion: %s", decimal_value)
            return 0.0

    def _str_to_decimal(self, value):
        """
        Convert value to Decimal with proper validation.
        """
        try:
            if isinstance(value, Decimal):
                return value
            if isinstance(value, (float, int)):
                return Decimal(str(value))
            if isinstance(value, str):
                return Decimal(value.replace(',', '.'))
            return Decimal('0')
        except (InvalidOperation, ValueError, TypeError):
            _logger.warning("Invalid value for decimal conversion: %s", value)
            return Decimal('0')

    def _apply_interest_precise(self, base_amount, interest_rate):
        """Aplica el interés manteniendo precisión."""
        base = self._str_to_decimal(base_amount)
        rate = self._str_to_decimal(interest_rate)
        return base * (1 + rate)
    
class SaleOrderLine(models.Model):
    
    _inherit = ['sale.order.line', 'fiserv.precision.mixin']
    _name = 'sale.order.line'

    fiserv_original_price = fields.Monetary(
        string='Precio Original',
        currency_field='currency_id',
        readonly=True,
        store=True
    )
    
    fiserv_interest_coefficient = fields.Float(
        string='Coeficiente de Interés',
        digits=(16, 6),
        readonly=True,
        store=True,
        help='Coeficiente aplicado al precio por financiación en cuotas'
    )

    fiserv_total_with_interest = fields.Float(
        string='Precio con Interés',
        digits=(16, 6),
        store=True,
        readonly=True,
        help='Precio unitario incluyendo el interés por cuotas'
    )

    is_fiserv_adjustment = fields.Boolean(
        string='Es ajuste Fiserv',
        default=False,
        readonly=True,
        copy=False,
        help='Identifica las líneas de ajuste por redondeo de Fiserv'
    )
    
    @api.depends('product_uom_qty', 'price_unit', 'tax_id')
    def _compute_amount(self):
        """
        Compute the amounts of the SO line.
        Handles precise decimal calculations when Fiserv interest is being applied.
        """
        for line in self:
            if not line.order_id._context.get('fiserv_adjusting_interest'):
                super()._compute_amount()
                continue
                
            try:
                line._compute_line_amounts_precise()
            except Exception as e:
                _logger.exception("Error computing precise amounts: %s", str(e))
                super()._compute_amount()

    def _compute_line_amounts_precise(self):
        """
        Compute line amounts with precise decimal handling.
        Stores original prices and updates computed fields.
        """
        self.ensure_one()
        
        try:
            # Store original price before any modifications
            if not self.fiserv_original_price:
                self.fiserv_original_price = self.price_unit
            
            price_unit = self._str_to_decimal(self.price_unit)
            quantity = self._str_to_decimal(self.product_uom_qty)
            
            # Calculate subtotal
            subtotal = price_unit * quantity
            
            # Handle taxes with precision
            if self.tax_id:
                taxes = self.tax_id.compute_all(
                    self._decimal_to_float(price_unit),
                    self.order_id.currency_id,
                    self._decimal_to_float(quantity),
                    product=self.product_id,
                    partner=self.order_id.partner_shipping_id
                )
                
                total = self._str_to_decimal(taxes['total_included'])
                
                # Update computed fields
                vals = {
                    'price_subtotal': self._decimal_to_float(subtotal),
                    'price_total': self._decimal_to_float(total),
                    'price_tax': self._decimal_to_float(total - subtotal)
                }
            else:
                vals = {
                    'price_subtotal': self._decimal_to_float(subtotal),
                    'price_total': self._decimal_to_float(subtotal),
                    'price_tax': 0.0
                }
                
            # Update fields and trigger recomputation
            self.write(vals)
            
        except Exception as e:
            _logger.exception("Error in precise amount computation: %s", str(e))
            raise
        
class SaleOrder(models.Model):
    
    _inherit = ['sale.order', 'fiserv.precision.mixin']
    _name = 'sale.order'
             
    payment_transaction_count = fields.Integer(
        string='Número de transacción',
        compute='_compute_payment_transaction_count',
        store=False
    )

    transaction_ids = fields.Many2many(
        'payment.transaction',
        'sale_order_transaction_rel',
        'sale_order_id',
        'transaction_id',
        string='Transactions',
        copy=False,
        readonly=True
    )    
    
    fiserv_payment_data = fields.Text(
        string='Datos del pago',
        compute='_compute_fiserv_payment_data',
        store=False
    )
    
    fiserv_card_brand = fields.Selection(
        selection=lambda self: [(code, data['name']) 
                              for code, data in const.SUPPORTED_CARD_BRANDS.items()],
        string='Marca',
        compute='_compute_fiserv_payment_data'
    )
    
    fiserv_installments = fields.Integer(
        string='Cuotas',
        compute='_compute_fiserv_payment_data',
        store=False
    )
    
    fiserv_card_number = fields.Char(
        string='Nro de tarjeta (últimos 4 nros)',
        compute='_compute_fiserv_payment_data',
        store=False
    )
    
    fiserv_total_with_interest = fields.Monetary(
        string='Total con interés',
        compute='_compute_fiserv_payment_data',
        store=False
    )
    
    fiserv_interest_amount = fields.Monetary(
        string='Monto del interés',
        compute='_compute_fiserv_interest_amount',
        store=True,
        currency_field='currency_id',
        help='Interest amount applied to installment payments'
    )
    
    payment_status = fields.Char(
        string='Estado del pago', 
        compute='_compute_payment_status'
    )
    
    fiserv_card_holder = fields.Char(
        string='Titular',
        compute='_compute_fiserv_payment_data',
        store=False
    )

    fiserv_transaction_id = fields.Char(
        string='ID transacción',
        compute='_compute_fiserv_payment_data',
        store=False
    )

    fiserv_amount_adjusted = fields.Boolean(
        string='Montos Ajustados',
        help='Indica si los montos fueron ajustados para coincidir con Fiserv',
        readonly=True,
        copy=False,
        store=True,
        groups='base.group_system'
    )

    is_fiserv_adjustment = fields.Boolean(
        string='Es ajuste Fiserv',
        default=False,
        readonly=True,
        copy=False,
        help='Identifica las líneas de ajuste por redondeo de Fiserv'
    )
           
    @api.depends(
        'order_line.price_total', 
        'order_line.price_unit', 
        'order_line.product_uom_qty', 
        'order_line.tax_id',
        'transaction_ids.state', 
        'transaction_ids.fiserv_total_with_interest',
        'fiserv_amount_adjusted'
    )
        
    def _compute_amounts(self):
        """
        Compute order amounts maintaining decimal precision for Fiserv transactions.
        """
        for order in self:
            # Avoid recursive computation
            if order._context.get('computing_amounts'):
                return
                
            ctx = dict(self._context, computing_amounts=True)
            
            try:
                # Get Fiserv transaction if exists
                tx = order._get_fiserv_transaction()
                
                if tx and tx.fiserv_total_with_interest and not order.fiserv_amount_adjusted:
                    if not order._context.get('fiserv_adjusting_interest'):
                        return super(SaleOrder, order.with_context(ctx))._compute_amounts()
                        
                # Calculate base amounts
                amount_untaxed = Decimal('0')
                amount_tax = Decimal('0')
                
                for line in order.order_line:
                    price_unit = order._str_to_decimal(line.price_unit)
                    quantity = order._str_to_decimal(line.product_uom_qty)
                    
                    subtotal = price_unit * quantity
                    amount_untaxed += subtotal
                    
                    if line.tax_id:
                        taxes = line.tax_id.compute_all(
                            order._decimal_to_float(price_unit),
                            order.currency_id,
                            order._decimal_to_float(quantity),
                            product=line.product_id,
                            partner=order.partner_shipping_id
                        )
                        total = order._str_to_decimal(taxes['total_included'])
                        tax_amount = total - subtotal
                        amount_tax += tax_amount

                # Update amounts
                order.amount_untaxed = order._decimal_to_float(amount_untaxed)
                order.amount_tax = order._decimal_to_float(amount_tax)
                current_total = amount_untaxed + amount_tax
                order.amount_total = order._decimal_to_float(current_total)

                # Handle Fiserv adjustment if needed
                if tx and tx.fiserv_total_with_interest:
                    order._handle_fiserv_adjustment(current_total, tx)

            except Exception as e:
                _logger.exception("Error computing amounts: %s", str(e))
                return super(SaleOrder, order.with_context(ctx))._compute_amounts()

    def _update_amounts_with_interest(self):
        """
        Updates order amounts with Fiserv transaction interest.
        Ensures proper synchronization between transaction and order amounts.
        """
        self.ensure_one()
        
        # Avoid recursive updates
        if self._context.get('updating_amounts_with_interest'):
            return
            
        tx = self._get_fiserv_transaction()
        if not tx or not tx.fiserv_total_with_interest:
            return
            
        try:
            ctx = dict(self._context, 
                    updating_amounts_with_interest=True,
                    fiserv_adjusting_interest=True)
                    
            with self.env.cr.savepoint():
                fiserv_total = self._str_to_decimal(tx.fiserv_total_with_interest)
                original_total = self._str_to_decimal(self.amount_total)
                
                if fiserv_total <= original_total:
                    return
                    
                # Calculate adjustment factor
                adjustment_factor = fiserv_total / original_total
                
                # Update lines in batch to improve performance
                update_vals = []
                for line in self.order_line.filtered(lambda l: not l.is_fiserv_adjustment):
                    original_price = self._str_to_decimal(line.price_unit)
                    new_price = original_price * adjustment_factor
                    
                    update_vals.append((1, line.id, {
                        'fiserv_original_price': float(original_price),
                        'price_unit': self._decimal_to_float(new_price),
                        'fiserv_interest_coefficient': float(adjustment_factor)
                    }))
                
                if update_vals:
                    self.with_context(ctx).write({'order_line': update_vals})
                    
                # Recompute amounts with new context
                self.with_context(ctx)._compute_amounts()
                
        except Exception as e:
            _logger.exception("Error updating amounts with interest: %s", str(e))
                                
    def _handle_fiserv_adjustment(self, current_total, tx):
        """
        Handle amount adjustments for Fiserv transactions.
        Centralizes adjustment logic in one place.
        """
        fiserv_total = self._str_to_decimal(tx.fiserv_total_with_interest)
        difference = fiserv_total - current_total

        # Log calculation details
        self._log_fiserv_calculation(current_total, fiserv_total, difference)

        if abs(difference) <= Decimal('0.001'):
            self.fiserv_amount_adjusted = True
            return

        self._handle_adjustment_line(self, difference, fiserv_total)

    def _log_fiserv_calculation(self, current_total, fiserv_total, difference):
        """
        Centralized logging for Fiserv calculations
        """
        log_data = {
            'timestamp': fields.Datetime.now(),
            'calculation_info': {
                'order_id': self.id,
                'order_name': self.name,
                'fiserv_total': float(fiserv_total),
                'calculated_total': float(current_total),
                'difference': float(difference)
            }
        }
        self.env['fiserv.transaction.log'].sudo().save_transaction_log(
            log_data, 
            filename_prefix='fiserv_calculate'
        )
    
    def _handle_adjustment_line(self, order, difference, fiserv_total):
        """Handles the creation or update of the adjustment line."""
        adjustment_line = order.order_line.filtered(
            lambda l: l.is_fiserv_adjustment
        )
        
        adjustment_product = self.env['product.product'].sudo().search(
            [('default_code', '=', 'AJUSTE_RED')], limit=1
        )
        
        if not adjustment_product:
            return
        
        tax_factor = Decimal('1.21')
        adjusted_price = difference / tax_factor
        
        if adjustment_line:
            if abs(difference) >= Decimal('0.01'):
                adjustment_line.write({
                    'price_unit': self._decimal_to_float(adjusted_price),
                    'product_uom_qty': 1.0
                })
        else:
            order.write({
                'order_line': [(0, 0, {
                    'product_id': adjustment_product.id,
                    'name': 'Ajuste por redondeo tarjetas',
                    'product_uom_qty': 1.0,
                    'price_unit': self._decimal_to_float(adjusted_price),
                    'is_fiserv_adjustment': True,
                    'sequence': 999,
                })]
            })
        
        order.amount_total = self._decimal_to_float(fiserv_total)
        order.fiserv_amount_adjusted = True 

    def _adjust_amounts_to_match_fiserv(self, difference):
        """Adjust amounts to match Fiserv total."""
        # Solo realizar ajustes en borrador y durante el checkout
        if not self.state in ['draft', 'sent'] or not self._context.get('fiserv_adjusting_interest'):
            return
            
        if not self.order_line:
            return
                
        total_base = sum(line.price_subtotal for line in self.order_line)
        if not total_base:
            return
            
        for line in self.order_line:
            line_proportion = line.price_subtotal / total_base
            line_adjustment = difference * line_proportion
            if line.product_uom_qty:
                new_price = line.price_unit + (line_adjustment / line.product_uom_qty)
                line.write({
                    'price_unit': new_price,
                    'fiserv_total_with_interest': new_price
                })
                                   
    def action_confirm(self):
        """Override the order confirmation to validate Fiserv settings."""
        for order in self:
            tx = order.transaction_ids.filtered(
                lambda t: t.provider_code == 'fiserv' and t.state == 'done'
            ).sorted('create_date', reverse=True)[:1]
            
            if tx and tx.fiserv_total_with_interest:
                try:
                    # Intentar ajustar los montos si es necesario
                    if not order.fiserv_amount_adjusted:
                        order.with_context(fiserv_adjusting_interest=True)._compute_amounts()
                    
                    # Verificar la diferencia con más tolerancia
                    difference = abs(order.amount_total - tx.fiserv_total_with_interest)
                    if difference > 1.0:  # Aumentamos la tolerancia a 1 peso
                        _logger.warning(
                            "Diferencia significativa en montos: Orden %s, Diferencia: %s", 
                            order.name, difference
                        )
                        # Registrar la advertencia pero no bloquear
                        order.message_post(body=_(
                            "Advertencia: Existe una diferencia de %s entre el monto de la orden "
                            "y el pago procesado por Fiserv") % difference
                        )

                except Exception as e:
                    _logger.error("Error al ajustar montos Fiserv: %s", str(e))
                    # Continuar con la confirmación a pesar del error
                        
            return super().action_confirm()

    @api.depends('transaction_ids', 'amount_total')
    def _compute_fiserv_interest_amount(self):
        """
        Compute interest amount from stored transaction value.
        This method is separate to avoid unnecessary recomputations.
        """
        for order in self:
            tx = order._get_fiserv_transaction()
            if tx and tx.fiserv_interest_amount:
                order.fiserv_interest_amount = tx.fiserv_interest_amount
            else:
                order.fiserv_interest_amount = 0.0
    
    @api.model
    def ensure_fiserv_fields_exist(self):
        """Ensure required Fiserv fields exist in the database."""
        logger = self.env['fiserv.transaction.log'].sudo()
        try:
            # Check and create fields if needed
            fields_to_check = {
                'fiserv_interest_amount': {
                    'type': 'monetary',
                    'string': 'Interest Amount',
                    'store': True,
                    'readonly': True
                },
                'fiserv_card_holder': {
                    'type': 'char',
                    'string': 'Card Holder',
                    'store': True,
                    'readonly': True
                },
                'fiserv_card_number': {
                    'type': 'char',
                    'string': 'Card Number',
                    'store': True,
                    'readonly': True,
                    'size': 4
                }
            }

            # Log field verification process
            log_data = {
                'timestamp': fields.Datetime.now(),
                'action': 'field_verification',
                'fields_checked': list(fields_to_check.keys())
            }

            cr = self.env.cr
            for field_name, field_def in fields_to_check.items():
                cr.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'sale_order' 
                    AND column_name = %s
                """, (field_name,))
                
                if not cr.fetchone():
                    log_data[f'creating_field_{field_name}'] = field_def
                    
                    # Create field if it doesn't exist
                    field_type = field_def['type']
                    sql = f"""
                        ALTER TABLE sale_order 
                        ADD COLUMN {field_name} {field_type}
                    """
                    cr.execute(sql)
                    
            logger.save_transaction_log(
                log_data,
                filename_prefix='field_verification'
            )
            
            return True
            
        except Exception as e:
            logger.log_error({
                'error_type': 'field_verification_error',
                'error_message': str(e)
            })
            return False

            
    def _get_fiserv_transaction(self):
        """
        Helper method to get the latest valid Fiserv transaction.
        Returns: payment.transaction record or False
        """
        self.ensure_one()
        return self.transaction_ids.filtered(
            lambda t: t.provider_code == 'fiserv' and t.state == 'done'
        ).sorted('create_date', reverse=True)[:1]  
                      
        
    @api.depends('transaction_ids')
    def _compute_payment_transaction_count(self):
        """
        Compute the total number of payment transactions linked to the order.
        This computed field updates automatically when transactions are added or removed.
        Useful for displaying transaction count in views and determining if an order
        has any associated payments.
        
        Performance optimization:
        - Skips computation for orders without transactions
        - Uses filtered domain to count only relevant transactions
        """
        for order in self:
            if not order.transaction_ids:
                order.payment_transaction_count = 0
                continue
                
            # Optional: Count only transactions in relevant states
            order.payment_transaction_count = len(order.transaction_ids.filtered(
                lambda t: t.state != 'draft'
            ))
        
    @api.depends('transaction_ids', 'transaction_ids.fiserv_total_with_interest',
                 'transaction_ids.fiserv_installments', 'amount_total')
    def _compute_fiserv_payment_data(self):
        """
        Compute Fiserv payment related fields from transaction data.
        Updates card holder, card number, interest and other payment information.
        """
        for order in self:
            tx = order._get_fiserv_transaction()
            if tx:
                # Format card number to show only last 4 digits
                card_number = tx.fiserv_card_number[-4:] if tx.fiserv_card_number else ''
                # Calculate total with interest and interest amount
                total_with_interest = (
                    tx.fiserv_total_with_interest 
                    if tx.fiserv_installments > 1 
                    else order.amount_total
                )
                interest_amount = (
                    tx.fiserv_interest_amount 
                    if tx.fiserv_installments > 1 
                    else 0.0
                )
                order.update({
                    'fiserv_card_brand': tx.fiserv_card_brand,
                    'fiserv_card_holder': tx.fiserv_card_holder or tx.partner_id.name,
                    'fiserv_transaction_id': tx.fiserv_txn_id,
                    'fiserv_installments': tx.fiserv_installments,
                    'fiserv_card_number': card_number,
                    'fiserv_total_with_interest': total_with_interest,
                })
                # Update payment data text
                order.fiserv_payment_data = f"""
                Transaction ID: {tx.fiserv_txn_id or ''}
                Approval Code: {tx.fiserv_approval_code or ''}
                Card: {tx.fiserv_card_brand or ''} **** {card_number}
                Holder: {order.fiserv_card_holder}
                Status: {tx.state}
                {f'Installments: {tx.fiserv_installments}' if tx.fiserv_installments > 1 else ''}
                """
            else:
                # Reset all fields if no valid transaction exists
                order.update({
                    'fiserv_card_brand': False,
                    'fiserv_card_holder': False,
                    'fiserv_transaction_id': False,
                    'fiserv_installments': 0,
                    'fiserv_card_number': False,
                    'fiserv_total_with_interest': 0.0,
                    'fiserv_payment_data': False
                })

    @api.depends('transaction_ids.state')
    def _compute_payment_status(self):
        """
        Compute the payment status for orders based on related transactions.
        Updates the payment_status field with the most recent transaction state.
        Only considers transactions in 'done', 'authorized', or 'pending' states.
        
        Returns:
            The state of the most recent relevant transaction, or False if none exists.
        """
        for order in self:
            relevant_transaction = order.transaction_ids.filtered(
                lambda t: t.state in ['done', 'authorized', 'pending']
            ).sorted('create_date', reverse=True)[:1]
            order.payment_status = relevant_transaction.state if relevant_transaction else False

    def _get_payment_status_message(self):
        """
        Generate a descriptive payment status message for Fiserv transactions.
        Formats message differently based on whether payment includes installments.
        
        Returns:
            str: A formatted message containing:
                - Card brand used
                - Number of installments (if applicable)
                - Installment amount (if applicable)
                - Falls back to parent implementation if not a Fiserv payment
        """
        self.ensure_one()
        tx = self.transaction_ids.filtered(
            lambda t: t.provider_code == 'fiserv' and t.state in ['done', 'authorized']
        ).sorted('create_date', reverse=True)[:1]
        
        if tx and self.fiserv_card_brand:
            card_brand = dict(self._fields['fiserv_card_brand'].selection).get(self.fiserv_card_brand)
            if self.fiserv_installments > 1:
                installment_amount = self.fiserv_total_with_interest / self.fiserv_installments
                return _('Paid with %s in %s installments of %s') % (
                    card_brand,
                    self.fiserv_installments,
                    self.currency_id.symbol + '%.2f' % installment_amount
                )
            return _('Paid with %s') % card_brand
        return super()._get_payment_status_message()

    def action_fiserv_payment_info(self):
        """
        Open a popup window displaying detailed payment information.
        Shows information for the most recent successful Fiserv transaction.
        
        Returns:
            dict: Action dictionary for opening the payment transaction form view
                in a new window.
        """
        self.ensure_one()
        return {
            'name': _('Payment Information'),
            'type': 'ir.actions.act_window',
            'res_model': 'payment.transaction',
            'view_mode': 'form',
            'res_id': self.transaction_ids.filtered(
                lambda t: t.provider_code == 'fiserv' and t.state in ['done', 'authorized']
            ).sorted('create_date', reverse=True)[:1].id,
            'target': 'new',
        }

    def _confirm_fiserv_payment(self):
        """
        Confirm sale order after successful Fiserv payment processing.
        
        Updates order amounts with final payment values including interest if applicable.
        Triggers order confirmation and email notification.
        
        Returns:
            bool: True if confirmation successful, False if error occurs
            
        Raises:
            Logs any exceptions through fiserv.transaction.log instead of raising
        """
        self.ensure_one()
        logger = self.env['fiserv.transaction.log'].sudo()
        
        try:
            tx = self.transaction_ids.filtered(
                lambda t: t.state == 'done' and t.provider_code == 'fiserv'
            )[:1]
            
            if tx and tx.fiserv_total_with_interest:
                self.write({
                    'amount_total': tx.fiserv_total_with_interest,
                    'amount_paid': tx.fiserv_total_with_interest
                })
                
            if self.state in ['draft', 'sent']:
                self.with_context(send_email=True).action_confirm()
                
            return True
            
        except Exception as e:
            logger.log_error({
                'order_id': self.id,
                'error_type': 'order_confirmation_error',
                'error_message': str(e)
            })
            return False